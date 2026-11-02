#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "sdkconfig.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "driver/usb_serial_jtag.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "rid_frame.h"
#include "rid_drain.h"

typedef struct {
    uint8_t data[RID_MAX_FRAME];
    size_t length;
    unsigned channel;
    int rssi;
    int64_t received_us;
} packet_t;
static QueueHandle_t packets, output;
typedef struct { char data[RID_JSON_MAX]; } output_record_t;
static rid_counters_t counters;
static portMUX_TYPE counter_lock = portMUX_INITIALIZER_UNLOCKED;
static char receiver_id[65], session_id[33];
#ifdef CONFIG_OUITAK_SCAN_HOP
#define SCAN_HOPPING true
#else
#define SCAN_HOPPING false
#endif
/* Channel state shared by the Wi-Fi callback, hop task and decode loop; all
 * guarded by counter_lock. switching brackets an esp_wifi_set_channel call. */
static rid_scan_t scan;
static unsigned tuned_channel, previous_channel;
static bool switching;
static uint64_t sequence;
static char json[RID_JSON_MAX]; /* Only app_main/output task owns this buffer. */
#define INCREMENT(field) do { portENTER_CRITICAL(&counter_lock); counters.field++; portEXIT_CRITICAL(&counter_lock); } while (0)

static rid_envelope_t envelope(void) {
    return (rid_envelope_t){receiver_id, session_id, sequence++, esp_timer_get_time()};
}
static void output_drop(void) {
    portENTER_CRITICAL(&counter_lock);
    counters.output_errors++; counters.usb_output_drops++;
    portEXIT_CRITICAL(&counter_lock);
}
/* USB backpressure cannot block the receive/decode loop. Output is lossy.
 * The bounded USB driver and bounded queue also bound disconnected memory use. */
static void output_task(void *unused) {
    (void)unused;
    output_record_t record;
    bool resync = false;
    for (;;) {
        if (xQueueReceive(output, &record, portMAX_DELAY) != pdTRUE) continue;
        if (resync) {
            if (usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(20)) != 1) {
                output_drop(); continue;
            }
            resync = false;
        }
        size_t length = strlen(record.data);
        int written = usb_serial_jtag_write_bytes(record.data, length, pdMS_TO_TICKS(20));
        if (written != (int)length) { output_drop(); resync = true; }
    }
}
static void emit(bool complete) {
    output_record_t record;
    if (!complete) { INCREMENT(output_errors); return; }
    if (!output) { output_drop(); return; }
    memcpy(record.data, json, strlen(json) + 1);
    if (xQueueSend(output, &record, 0) != pdTRUE) output_drop();
}
static bool checked(esp_err_t error, const char *operation) {
    if (error == ESP_OK) return true;
    INCREMENT(errors);
    rid_envelope_t e = envelope();
    emit(rid_json_error(json, sizeof(json), &e, operation, error));
    return false;
}
static void on_packet(void *buffer, wifi_promiscuous_pkt_type_t type) {
    if (!buffer || type != WIFI_PKT_MGMT) return;
    const wifi_promiscuous_pkt_t *rx = buffer;
    unsigned rx_channel = rx->rx_ctrl.channel;
    portENTER_CRITICAL(&counter_lock);
    counters.rf_frames++;
    rid_scan_frame(&scan, rx_channel);
    /* While hopping, a frame tagged with the channel being left (or received
     * mid-switch) is a channel transition, not a metadata error: drop it
     * without counting it as malformed. Other mismatches stay rejections. */
    bool transition = SCAN_HOPPING && rx_channel != tuned_channel &&
                      (switching || rx_channel == previous_channel);
    if (transition) rid_scan_transition_drop(&scan);
    unsigned tuned = tuned_channel;
    portEXIT_CRITICAL(&counter_lock);
    if (transition) return;
    /* MPDU is sig_len - 4 on C5/IDF v5.5.4 (see rid_capture_check). Do not use
     * the legacy 4-bit channel or infer FCS from other ESP chips. */
    size_t n;
    rid_capture_t capture = rid_capture_check(rx->rx_ctrl.sig_len, rx->rx_ctrl.dump_len, &n);
    unsigned fc = rx->rx_ctrl.sig_len >= 2 ? (unsigned)rx->payload[0] << 8 | rx->payload[1] : 0;
    bool bad_state = rx->rx_ctrl.rx_state, bad_length = capture == RID_CAPTURE_BAD_LENGTH;
    bool bad_channel = rx_channel != tuned, bad_rssi = rx->rx_ctrl.rssi > 0 || rx->rx_ctrl.rssi < -127;
    if (bad_state || bad_length || bad_channel || bad_rssi) {
        /* Bring-up diagnostic: attribute the rejection and keep the raw metadata. */
        portENTER_CRITICAL(&counter_lock);
        counters.errors++; counters.malformed_frames++;
        counters.rx_reject_state += bad_state; counters.rx_reject_length += bad_length;
        counters.rx_reject_channel += bad_channel; counters.rx_reject_rssi += bad_rssi;
        counters.last_reject.valid = true;
        counters.last_reject.rx_state = rx->rx_ctrl.rx_state;
        counters.last_reject.rxend_state = rx->rx_ctrl.rxend_state;
        counters.last_reject.sig_len = rx->rx_ctrl.sig_len;
        counters.last_reject.dump_len = rx->rx_ctrl.dump_len;
        counters.last_reject.channel = rx->rx_ctrl.channel;
        counters.last_reject.second = rx->rx_ctrl.second;
        counters.last_reject.bb_format = rx->rx_ctrl.cur_bb_format;
        counters.last_reject.frame_control = fc;
        counters.last_reject.rssi = rx->rx_ctrl.rssi;
        portEXIT_CRITICAL(&counter_lock);
        return;
    }
    portENTER_CRITICAL(&counter_lock);
    if (n > counters.max_frame_len) counters.max_frame_len = n > UINT32_MAX ? UINT32_MAX : (uint32_t)n;
    if (capture == RID_CAPTURE_OVERSIZE) {
        counters.oversize_drops++;
        counters.last_oversize.valid = true;
        counters.last_oversize.length = n;
        counters.last_oversize.channel = rx_channel;
        counters.last_oversize.frame_control = fc;
    }
    portEXIT_CRITICAL(&counter_lock);
    if (capture == RID_CAPTURE_OVERSIZE) return;
    /* Driver task callback, not an ISR. No decode, heap allocation or printing.
     * Static, not on the Wi-Fi task stack: RID_MAX_FRAME is ~2.3 KB. This
     * callback is only ever invoked from the single Wi-Fi task, never
     * concurrently, and xQueueSend copies the packet before returning. */
    static packet_t packet;
    packet.length = n; packet.channel = rx_channel;
    packet.rssi = rx->rx_ctrl.rssi; packet.received_us = esp_timer_get_time();
    memcpy(packet.data, rx->payload, n);
    if (xQueueSend(packets, &packet, 0) != pdTRUE) INCREMENT(queue_drops);
}

/* Hopping only: tune the next scheduled channel every dwell period. Subscribed
 * to the task watchdog so a stalled channel switch reboots like a stalled decoder. */
static void hop_task(void *unused) {
    (void)unused;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(CONFIG_OUITAK_SCAN_DWELL_MS));
        portENTER_CRITICAL(&counter_lock);
        unsigned next = rid_scan_next(&scan, esp_timer_get_time());
        bool change = next != tuned_channel;
        switching = change;
        portEXIT_CRITICAL(&counter_lock);
        if (!change) continue;
        int64_t started = esp_timer_get_time();
        uint8_t actual = 0;
        wifi_second_chan_t secondary;
        bool ok = esp_wifi_set_channel(next, WIFI_SECOND_CHAN_NONE) == ESP_OK &&
                  esp_wifi_get_channel(&actual, &secondary) == ESP_OK && actual == next;
        int64_t done = esp_timer_get_time();
        uint32_t elapsed = done - started > UINT32_MAX ? UINT32_MAX : (uint32_t)(done - started);
        portENTER_CRITICAL(&counter_lock);
        if (!ok) rid_scan_tune_failed(&scan);
        /* On failure follow the read-back channel if it is a scan channel, so the
         * callback never compares frames against a channel the radio left. */
        unsigned now_on = ok ? next : actual;
        if (now_on != tuned_channel && rid_scan_index(&scan, now_on) >= 0) {
            previous_channel = tuned_channel;
            tuned_channel = now_on;
            rid_scan_tuned(&scan, now_on, done, elapsed);
        }
        switching = false;
        portEXIT_CRITICAL(&counter_lock);
    }
}

static bool start_receiver(void) {
    if (!rid_scan_init(&scan, SCAN_HOPPING, CONFIG_OUITAK_CHANNEL, CONFIG_OUITAK_SCAN_DWELL_MS, CONFIG_OUITAK_SCAN_HOT_TTL_S))
        return checked(ESP_ERR_INVALID_ARG, SCAN_HOPPING ? "scan_config" : "channel_allowlist");
    unsigned first = rid_scan_next(&scan, esp_timer_get_time());
    if (strlen(CONFIG_OUITAK_COUNTRY) != 2) return checked(ESP_ERR_INVALID_ARG, "country_length");
    /* No erase/recovery if NVS initialization fails. Calibration may use NVS;
     * Wi-Fi settings persistence is disabled. No factory restore operations. */
    if (!checked(nvs_flash_init(), "nvs_init")) return false;
    if (!checked(esp_event_loop_create_default(), "event_loop")) return false;
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    config.nvs_enable = false;
    if (!checked(esp_wifi_init(&config), "wifi_init") ||
        !checked(esp_wifi_set_storage(WIFI_STORAGE_RAM), "storage_ram") ||
        !checked(esp_wifi_set_mode(WIFI_MODE_NULL), "mode_null")) return false;
    if (!checked(esp_wifi_set_country_code(CONFIG_OUITAK_COUNTRY, false), "country") ||
        !checked(esp_wifi_start(), "wifi_start") ||
        !checked(esp_wifi_set_band_mode(SCAN_HOPPING ? WIFI_BAND_MODE_AUTO :
                                        first <= 11 ? WIFI_BAND_MODE_2G_ONLY : WIFI_BAND_MODE_5G_ONLY), "band_mode") ||
        !checked(esp_wifi_set_channel(first, WIFI_SECOND_CHAN_NONE), SCAN_HOPPING ? "first_channel" : "fixed_channel")) return false;
    uint8_t actual;
    wifi_second_chan_t secondary;
    wifi_band_t band;
    if (!checked(esp_wifi_get_channel(&actual, &secondary), "readback_channel") ||
        !checked(esp_wifi_get_band(&band), "readback_band")) return false;
    if (actual != first || (actual <= 11 ? band != WIFI_BAND_2G : band != WIFI_BAND_5G))
        return checked(ESP_FAIL, "tune_mismatch");
    portENTER_CRITICAL(&counter_lock);
    tuned_channel = previous_channel = actual;
    rid_scan_tuned(&scan, actual, esp_timer_get_time(), 0);
    portEXIT_CRITICAL(&counter_lock);
    wifi_promiscuous_filter_t filter = {.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT};
    return checked(esp_wifi_set_promiscuous_filter(&filter), "mgmt_filter") &&
           checked(esp_wifi_set_promiscuous_rx_cb(on_packet), "callback") &&
           checked(esp_wifi_set_promiscuous(true), "promiscuous_enable");
}

/* Decoding and JSON formatting happen in the consumer, never the Wi-Fi task. */
static bool receive_packet(void *context, unsigned wait_ms) {
    return xQueueReceive(packets, context, pdMS_TO_TICKS(wait_ms)) == pdTRUE;
}
static int64_t drain_clock(void *unused) { (void)unused; return esp_timer_get_time(); }
static void process_packet(void *context) {
    const packet_t *packet = context;
    rid_observation_t observation;
    rid_result_t result = rid_parse_frame(packet->data, packet->length, &observation);
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&counter_lock);
    rid_account_result(&counters, result, &observation);
    rid_scan_result(&scan, packet->channel, observation.candidate, result == RID_DECODED, now);
    portEXIT_CRITICAL(&counter_lock);
    /* Trackable packs become observations; every other strictly decoded pack is a
     * reception record. Candidates, malformed frames and decode failures emit nothing. */
    if (result == RID_DECODED) {
        rid_envelope_t e = envelope();
        bool complete = observation.detection
            ? rid_json_detection(json, sizeof(json), &e, &observation, packet->channel, packet->rssi, packet->received_us)
            : rid_json_reception(json, sizeof(json), &e, &observation, packet->channel, packet->rssi, packet->received_us);
        if (complete && observation.detection) INCREMENT(detections);
        else if (complete) INCREMENT(rid_receptions);
        emit(complete);
    }
}

void app_main(void) {
    /* No host handshake, console read, DTR/RTS operation, or USB-connected wait. */
    rid_json_set_reset_reason((int)esp_reset_reason());
    usb_serial_jtag_driver_config_t usb = {.tx_buffer_size = 4096, .rx_buffer_size = 256};
    if (usb_serial_jtag_driver_install(&usb) == ESP_OK) {
        output = xQueueCreate(4, sizeof(output_record_t));
        if (output && xTaskCreate(output_task, "usb_output", 6144, NULL, 4, NULL) != pdPASS) {
            vQueueDelete(output); output = NULL;
        }
    }
    /* IDF initializes the panic/reboot watchdog from sdkconfig. Subscribe the
     * decode task so a stalled loop recovers even when idle tasks still run. */
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    uint8_t mac[6];
    if (esp_efuse_mac_get_default(mac) != ESP_OK) return;
    snprintf(receiver_id, sizeof(receiver_id), "c5-%02x%02x%02x%02x%02x%02x", mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
    if (strlen(CONFIG_OUITAK_RECEIVER_ID) > 64) return;
    if (CONFIG_OUITAK_RECEIVER_ID[0]) snprintf(receiver_id, sizeof(receiver_id), "%s", CONFIG_OUITAK_RECEIVER_ID);
    uint8_t random[16];
    esp_fill_random(random, sizeof(random));
    for (unsigned i = 0; i < sizeof(random); i++) snprintf(session_id + 2 * i, 3, "%02x", random[i]);
    rid_envelope_t e = envelope();
    emit(rid_json_hello(json, sizeof(json), &e, CONFIG_OUITAK_CHANNEL, SCAN_HOPPING));
    packets = xQueueCreate(CONFIG_OUITAK_QUEUE_DEPTH, sizeof(packet_t));
    bool active = checked(packets ? ESP_OK : ESP_ERR_NO_MEM, "packet_queue") && start_receiver();
    if (active && SCAN_HOPPING && xTaskCreate(hop_task, "rid_hop", 3072, NULL, 5, NULL) != pdPASS)
        active = checked(ESP_ERR_NO_MEM, "hop_task");
    if (!active) {
        /* Fail closed; never reset/retry/reconfigure in an error loop. */
        esp_wifi_set_promiscuous(false);
        esp_wifi_stop();
    }
    int64_t last_status = 0;
    packet_t packet;
    for (;;) {
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        rid_drain_result_t drained = {0};
        if (active) drained = rid_drain(&packet, receive_packet, process_packet, drain_clock);
        else vTaskDelay(pdMS_TO_TICKS(100));
        if (esp_timer_get_time() - last_status >= 5000000) {
            static rid_scan_t scan_snapshot; /* app_main only; too large for a casual stack copy */
            rid_counters_t snapshot;
            unsigned channel;
            portENTER_CRITICAL(&counter_lock);
            snapshot = counters; scan_snapshot = scan; channel = tuned_channel;
            portEXIT_CRITICAL(&counter_lock);
            e = envelope();
            emit(rid_json_status(json, sizeof(json), &e, active ? "monitoring" : "stopped_error", channel,
                &snapshot, esp_get_free_heap_size(), esp_get_minimum_free_heap_size(),
                active ? &scan_snapshot : NULL, esp_timer_get_time()));
            last_status = esp_timer_get_time();
        }
        /* No per-packet sleep. Under continuous load, yield once per bounded
         * 32-packet / 5-ms slice, allowing idle, USB and watchdog housekeeping.
         * Empty queues naturally block in the next receive instead. */
        if (drained.yield_required) vTaskDelay(1);
    }
}
