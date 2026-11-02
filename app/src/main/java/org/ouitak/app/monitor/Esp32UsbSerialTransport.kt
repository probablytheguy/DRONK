package org.ouitak.app.monitor

import android.content.Context
import android.hardware.usb.*
import java.io.Closeable

/** USB CDC host for the ESP32-S3 native USB serial interface (303a:1001). */
class Esp32UsbSerialTransport(context: Context) {
    private val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager

    data class DeviceAccess(val device: UsbDevice, val permitted: Boolean)
    fun devices(): List<DeviceAccess> = manager.deviceList.values
        .filter { it.vendorId == ESPRESSIF_VID && it.productId == ESP32_S3_CDC_PID }
        .map { DeviceAccess(it, manager.hasPermission(it)) }

    fun requestPermission(device: UsbDevice, pendingIntent: android.app.PendingIntent) = manager.requestPermission(device, pendingIntent)

    fun open(device: UsbDevice): Connection {
        require(device.vendorId == ESPRESSIF_VID && device.productId == ESP32_S3_CDC_PID) { "Unsupported USB device" }
        val data = (0 until device.interfaceCount).map(device::getInterface).firstOrNull { intf ->
            intf.interfaceClass == UsbConstants.USB_CLASS_CDC_DATA &&
                (0 until intf.endpointCount).map(intf::getEndpoint).any {
                    it.type == UsbConstants.USB_ENDPOINT_XFER_BULK && it.direction == UsbConstants.USB_DIR_IN
                }
        } ?: error("ESP32-S3 CDC data interface not found")
        val control = (0 until device.interfaceCount).map(device::getInterface)
            .firstOrNull { it.interfaceClass == UsbConstants.USB_CLASS_COMM }
        val input = (0 until data.endpointCount).map(data::getEndpoint).first {
            it.type == UsbConstants.USB_ENDPOINT_XFER_BULK && it.direction == UsbConstants.USB_DIR_IN
        }
        val connection = manager.openDevice(device) ?: error("USB device could not be opened")
        try {
            if (control != null && !connection.claimInterface(control, true)) error("Could not claim CDC control interface")
            if (!connection.claimInterface(data, true)) error("Could not claim CDC data interface")
            if (control != null) {
                // CDC SET_LINE_CODING: 115200 baud, 1 stop bit, no parity, 8 data bits.
                val lineCoding = byteArrayOf(0x00, 0xC2.toByte(), 0x01, 0x00, 0, 0, 8)
                val result = connection.controlTransfer(0x21, 0x20, 0, control.id, lineCoding, lineCoding.size, 1000)
                if (result < 0) error("CDC 115200 8N1 configuration rejected")
            }
            // Do not toggle DTR/RTS: some USB-UART bridges wire these to reset.
            return Connection(connection, input, data, control)
        } catch (e: Exception) {
            connection.close()
            throw e
        }
    }

    class Connection internal constructor(
        private val connection: UsbDeviceConnection,
        private val input: UsbEndpoint,
        private val data: UsbInterface,
        private val control: UsbInterface?
    ) : Closeable {
        fun read(buffer: ByteArray): Int = connection.bulkTransfer(input, buffer, buffer.size, 1000)
        override fun close() {
            try { connection.releaseInterface(data) } catch (_: Exception) { }
            control?.let { try { connection.releaseInterface(it) } catch (_: Exception) { } }
            connection.close()
        }
    }

    companion object {
        const val ESPRESSIF_VID = 0x303A
        const val ESP32_S3_CDC_PID = 0x1001
    }
}
