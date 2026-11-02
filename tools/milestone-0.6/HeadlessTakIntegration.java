import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Enumeration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLPeerUnverifiedException;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManagerFactory;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.xml.sax.InputSource;
import java.io.StringReader;

/** Test-only remote delivery. Client keys are loaded in memory by JSSE from a local PKCS#12. */
public final class HeadlessTakIntegration {
    private static final String DEFAULT_CONNECT = System.getenv("DRONK_TAK_CONNECT_ADDRESS");
    private static final String DEFAULT_HOSTNAME = System.getenv("DRONK_TAK_HOST");
    private static final int DEFAULT_PORT = 8089;
    private static final String COT_NAMESPACE = "org.ouitak.remoteid.simulation.v1";
    private static final String AIRCRAFT_ID = "SIM-DRONE-001";
    private static final String UID = "ouitak-rid-" + UUID.nameUUIDFromBytes(
            (COT_NAMESPACE + "\u0000SERIAL_NUMBER\u0000" + AIRCRAFT_ID).getBytes(StandardCharsets.UTF_8));
    private static final DateTimeFormatter COT_TIME = new DateTimeFormatterBuilder().appendInstant(3).toFormatter();

    private HeadlessTakIntegration() {}

    public static void main(String[] args) {
        try {
            Config config = Config.parse(args);
            run(config);
        } catch (Exception error) {
            log("ERROR " + safeFailure(error));
            System.exit(2);
        }
    }

    private static void run(Config config) throws Exception {
        SSLContext context = createContext(config.clientP12, config.passwordFile, config.caFile);
        java.net.Socket tcp = new java.net.Socket();
        tcp.connect(new InetSocketAddress(config.connectAddress, config.port), 10_000);
        SSLSocket socket;
        try { socket = (SSLSocket) context.getSocketFactory().createSocket(tcp, config.tlsHostname, config.port, true); }
        catch (Exception failure) { tcp.close(); throw failure; }
        AtomicBoolean peerClosed = new AtomicBoolean(false);
        try (socket) {
            socket.setTcpNoDelay(true);
            socket.setKeepAlive(true);
            socket.setSoTimeout(10_000);

            SSLParameters parameters = socket.getSSLParameters();
            parameters.setEndpointIdentificationAlgorithm("HTTPS");
            parameters.setServerNames(List.of(new javax.net.ssl.SNIHostName(config.tlsHostname)));
            parameters.setProtocols(Arrays.stream(socket.getSupportedProtocols())
                    .filter(protocol -> protocol.equals("TLSv1.3") || protocol.equals("TLSv1.2"))
                    .toArray(String[]::new));
            if (parameters.getProtocols().length == 0) throw new IOException("TLS 1.2 or newer is unavailable");
            socket.setSSLParameters(parameters);
            socket.startHandshake();

            Certificate[] localCertificates = socket.getSession().getLocalCertificates();
            if (localCertificates == null || localCertificates.length == 0) {
                throw new SSLPeerUnverifiedException("Server did not complete a client-certificate handshake");
            }
            log("TLS established; hostname and CA verified; client certificate presented; protocol=" +
                    socket.getSession().getProtocol() + "; cipher=" + socket.getSession().getCipherSuite());
            log("Endpoint privately configured; TLS certificate hostname verification enabled");

            socket.setSoTimeout(2_000);
            Thread monitor = monitorPeer(socket, peerClosed);
            if (config.preflight) {
                log("Preflight complete; no CoT event sent.");
                return;
            }

            String template = Files.readString(config.fixture, StandardCharsets.UTF_8);
            // Source files conventionally end with LF; TAK stream frames must not.
            if (template.endsWith("\r\n")) template = template.substring(0, template.length() - 2);
            else if (template.endsWith("\n")) template = template.substring(0, template.length() - 1);
            validateTemplate(template);
            OutputStream output = socket.getOutputStream();
            switch (config.mode) {
                case ICONS -> { sendIconComparison(template, output, peerClosed); return; }
                case FLIGHT -> { runFlight(template, output, peerClosed); return; }
                case LEGACY -> runLegacy(template, output, peerClosed, config);
            }
            Instant first = Instant.now().truncatedTo(ChronoUnit.MILLIS);
            Instant last = Instant.now().truncatedTo(ChronoUnit.MILLIS);
            Instant finalStale = last.plusSeconds(config.staleSeconds);
            long waitMillis = Math.max(0, Duration.between(Instant.now(), finalStale.plusSeconds(2)).toMillis());
            log("Updates complete; waiting " + waitMillis + " ms beyond the final event stale time " +
                    COT_TIME.format(finalStale) + "; no additional event or delete message will be sent");
            Thread.sleep(waitMillis);
            if (peerClosed.get()) log("TAK Server closed its stream after the writes");
            else log("Stale interval elapsed; TCP remained open; server-side ingestion is not acknowledged by this protocol");
            monitor.interrupt();
            try { monitor.join(1_000); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            log("Test finished; closing TLS stream. Updates used one stable OUI-TAK synthetic UID.");
            log("First event time=" + COT_TIME.format(first) + "; configured stale interval=" + config.staleSeconds + " s");
        }
    }

    private static void runLegacy(String template, OutputStream output, AtomicBoolean peerClosed, Config config) throws Exception {
        for (int i = 0; i < config.updates; i++) {
            if (peerClosed.get()) throw new IOException("TAK Server closed the stream during updates");
            double lat = 0.00101 + i * 0.0002, lon = 0.00201 + i * 0.0002;
            Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
            sendEvent(template, output, UID, AIRCRAFT_ID, "a-u-A", now, config.staleSeconds,
                    lat, lon, 30, 2, 45, i + 1, config.updates);
            if (i + 1 < config.updates) Thread.sleep(config.intervalMillis);
        }
    }

    private static final List<String> ICON_TYPES = List.of(
            "a-u-G-U-U-D", "a-u-A-M-H-R", "a-u-A-M-H-Q", "a-u-A-M-F-Q");

    private static void sendIconComparison(String template, OutputStream output, AtomicBoolean peerClosed) throws Exception {
        log("Sending four synthetic icon candidates near St. Louis for side-by-side comparison.");
        for (int i = 0; i < ICON_TYPES.size(); i++) {
            if (peerClosed.get()) throw new IOException("TAK Server closed the stream during icon comparison");
            String type = ICON_TYPES.get(i);
            String callsign = "SYNTH-ICON-" + (i + 1);
            String uid = stableUid("icon:" + type);
            Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
            sendEvent(template, output, uid, callsign, type, now, 180,
                    38.6270 + i * 0.00018, -90.1994 + i * 0.00018, 140, 0, 0, i + 1, ICON_TYPES.size());
            Thread.sleep(500);
        }
        log("Icon candidates sent; each has a 180 s stale time. Socket writes do not acknowledge TAK/client receipt.");
    }

    private static String stableUid(String name) {
        return "ouitak-test-" + UUID.nameUUIDFromBytes((COT_NAMESPACE + "\u0000" + name).getBytes(StandardCharsets.UTF_8));
    }

    private static void runFlight(String template, OutputStream output, AtomicBoolean peerClosed) throws Exception {
        // Ten synthetic waypoints form a compact, looping route near downtown St. Louis.
        final double[][] route = {
                {38.6270, -90.1994, 130, 8.0, 45}, {38.6282, -90.1982, 145, 10.5, 40},
                {38.6296, -90.1972, 170, 12.0, 30}, {38.6307, -90.1960, 195, 11.0, 42},
                {38.6312, -90.1942, 210, 9.0, 70}, {38.6306, -90.1925, 185, 8.0, 110},
                {38.6294, -90.1914, 160, 10.0, 145}, {38.6281, -90.1922, 140, 12.0, 205},
                {38.6270, -90.1940, 125, 9.5, 235}, {38.6265, -90.1962, 120, 7.0, 260}
        };
        String uid = stableUid("flight:SIM-DRONE-FLIGHT-001");
        String callsign = "SIM-DRONE-FLIGHT-001";
        AtomicBoolean started = new AtomicBoolean(false), paused = new AtomicBoolean(false), stopped = new AtomicBoolean(false);
        java.util.concurrent.atomic.AtomicInteger sequence = new java.util.concurrent.atomic.AtomicInteger(0);
        ScheduledExecutorService sender = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "ouitak-synthetic-flight"); thread.setDaemon(true); return thread;
        });
        java.io.BufferedReader console = new java.io.BufferedReader(new java.io.InputStreamReader(System.in));
        log("Flight simulation ready. Commands: start, pause, resume, stop, quit. UID=" + uid +
                "; update interval=5 s; stale=15 s; all data synthetic.");
        try {
            String line;
            while ((line = console.readLine()) != null) {
                String command = line.trim().toLowerCase(java.util.Locale.ROOT);
                switch (command) {
                    case "start" -> {
                        if (stopped.get()) { log("Simulation is stopped; run a new process to start again."); break; }
                        if (started.compareAndSet(false, true)) {
                            sender.scheduleAtFixedRate(() -> {
                                if (stopped.get() || paused.get() || peerClosed.get()) return;
                                int i = sequence.getAndIncrement();
                                double[] point = route[i % route.length];
                                try {
                                    Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
                                    sendEvent(template, output, uid, callsign, "a-u-A", now, 15,
                                            point[0], point[1], point[2], point[3], point[4], i + 1, -1);
                                } catch (Exception error) {
                                    stopped.set(true);
                                    log("ERROR flight transmission stopped; reason=" + error.getClass().getSimpleName());
                                }
                            }, 0, 5, TimeUnit.SECONDS);
                            log("Flight started.");
                        } else log("Flight already started; use resume if paused.");
                    }
                    case "pause" -> { if (!started.get() || stopped.get()) log("Flight is not running."); else { paused.set(true); log("Flight paused; last point will stale unless resumed."); } }
                    case "resume" -> { if (!started.get() || stopped.get()) log("Flight is not running."); else { paused.set(false); log("Flight resumed."); } }
                    case "stop" -> { stopped.set(true); sender.shutdownNow(); log("Flight stopped; no delete event sent. Last point will stale after 15 s."); return; }
                    case "quit" -> { stopped.set(true); sender.shutdownNow(); log("Flight session ended."); return; }
                    case "" -> { }
                    default -> log("Unknown command. Use start, pause, resume, stop, or quit.");
                }
            }
        } finally { stopped.set(true); sender.shutdownNow(); }
    }

    private static void sendEvent(String template, OutputStream output, String uid, String callsign, String type,
                                  Instant now, int staleSeconds, double lat, double lon, double altitude,
                                  double speed, double course, int index, int total) throws Exception {
        String event = render(template, uid, callsign, type, now, now.plusSeconds(staleSeconds), lat, lon, altitude, speed, course);
        validateEvent(event, uid, callsign, type, now, staleSeconds);
        byte[] frame = event.getBytes(StandardCharsets.UTF_8);
        synchronized (output) { output.write(frame); output.flush(); }
        log("CoT write " + (total < 0 ? "#" + index : index + "/" + total) + " uid=" + uid + " callsign=" + callsign +
                " type=" + type + " synthetic=true lat=" + fmtCoordinate(lat) + " lon=" + fmtCoordinate(lon) + " hae=" + fmt(altitude) +
                "m speed=" + fmt(speed) + "m/s course=" + fmt(course) + " stale=" + COT_TIME.format(now.plusSeconds(staleSeconds)) +
                " bytes=" + frame.length + "; application receipt is not acknowledged");
    }

    private static String fmt(double value) { return String.format(java.util.Locale.ROOT, "%.2f", value); }
    private static String fmtCoordinate(double value) { return String.format(java.util.Locale.ROOT, "%.5f", value); }

    private static SSLContext createContext(Path p12Path, Path passwordPath, Path caPath) throws Exception {
        char[] password = readPassword(passwordPath);
        try {
            KeyStore client = KeyStore.getInstance("PKCS12");
            try (InputStream input = Files.newInputStream(p12Path)) { client.load(input, password); }
            boolean hasPrivateKey = false;
            for (Enumeration<String> aliases = client.aliases(); aliases.hasMoreElements();) {
                String alias = aliases.nextElement();
                if (client.isKeyEntry(alias)) { hasPrivateKey = true; break; }
            }
            if (!hasPrivateKey) throw new IOException("Client PKCS#12 contains no private-key entry");

            KeyManagerFactory keyManagers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            keyManagers.init(client, password);
            KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
            trust.load(null, null);
            CertificateFactory factory = CertificateFactory.getInstance("X.509");
            List<? extends Certificate> certificates;
            try (InputStream input = Files.newInputStream(caPath)) {
                certificates = new ArrayList<>(factory.generateCertificates(input));
            }
            if (certificates.isEmpty() || certificates.size() > 32) throw new IOException("Trusted CA bundle is empty or invalid");
            int index = 0;
            for (Certificate certificate : certificates) {
                if (!(certificate instanceof X509Certificate x509) || x509.getBasicConstraints() < 0) {
                    throw new IOException("Trust bundle contains a non-CA certificate");
                }
                x509.checkValidity();
                trust.setCertificateEntry("trusted-ca-" + index++, x509);
            }
            TrustManagerFactory trustManagers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            trustManagers.init(trust);
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(keyManagers.getKeyManagers(), trustManagers.getTrustManagers(), null);
            log("Client PKCS#12 loaded in memory; trusted CA bundle loaded; credential values redacted");
            return context;
        } finally {
            Arrays.fill(password, '\0');
        }
    }

    private static char[] readPassword(Path path) throws Exception {
        byte[] bytes = Files.readAllBytes(path);
        int length = bytes.length;
        while (length > 0 && (bytes[length - 1] == '\n' || bytes[length - 1] == '\r')) length--;
        try {
            CharBuffer decoded = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes, 0, length));
            char[] result = new char[decoded.remaining()];
            decoded.get(result);
            if (decoded.hasArray()) Arrays.fill(decoded.array(), '\0');
            return result;
        } catch (CharacterCodingException error) {
            throw new IOException("Credential password file is not valid UTF-8");
        } finally {
            Arrays.fill(bytes, (byte) 0);
        }
    }

    private static Thread monitorPeer(SSLSocket socket, AtomicBoolean peerClosed) {
        Thread monitor = new Thread(() -> {
            byte[] buffer = new byte[2048];
            try {
                InputStream input = socket.getInputStream();
                while (!Thread.currentThread().isInterrupted() && !socket.isClosed()) {
                    try {
                        int count = input.read(buffer);
                        if (count < 0) {
                            peerClosed.set(true);
                            log("Server stream closed");
                            return;
                        }
                        // Server-side CoT/control bytes are intentionally not printed or parsed here.
                    } catch (java.net.SocketTimeoutException expected) {
                        // Poll close state without producing periodic log noise.
                    }
                }
            } catch (IOException error) {
                if (!socket.isClosed()) log("WARN server-stream read failed: " + error.getClass().getSimpleName());
            }
        }, "ouitak-headless-peer-monitor");
        monitor.setDaemon(true);
        monitor.start();
        return monitor;
    }

    private static void validateTemplate(String template) throws Exception {
        for (String token : List.of("{{UID}}", "{{TYPE}}", "{{CALLSIGN}}", "{{TIME}}", "{{STALE}}", "{{LAT}}", "{{LON}}", "{{ALT}}", "{{SPEED}}", "{{COURSE}}")) {
            if (!template.contains(token)) throw new IOException("Synthetic CoT fixture is missing a required field");
        }
        if (!template.startsWith("<?xml ") || !template.contains("?>\n<event") || template.endsWith("\n")) {
            throw new IOException("Fixture does not match TAK XML stream framing");
        }
        if (!template.contains("how=\"m-s\"") || !template.contains("synthetic=\"true\"") ||
                !template.contains("SIMULATION:")) throw new IOException("Fixture is not clearly marked synthetic");
    }

    private static String render(String template, String uid, String callsign, String type, Instant time, Instant stale,
                                 double lat, double lon, double altitude, double speed, double course) {
        return template.replace("{{UID}}", uid).replace("{{TYPE}}", type).replace("{{CALLSIGN}}", callsign)
                .replace("{{TIME}}", COT_TIME.format(time))
                .replace("{{STALE}}", COT_TIME.format(stale))
                .replace("{{LAT}}", Double.toString(lat))
                .replace("{{LON}}", Double.toString(lon)).replace("{{ALT}}", Double.toString(altitude))
                .replace("{{SPEED}}", Double.toString(speed)).replace("{{COURSE}}", Double.toString(course));
    }

    private static void validateEvent(String xml, String uid, String callsign, String type, Instant expectedTime, int staleSeconds) throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
        factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        factory.setXIncludeAware(false);
        factory.setExpandEntityReferences(false);
        Document document = factory.newDocumentBuilder().parse(new InputSource(new StringReader(xml)));
        Element event = document.getDocumentElement();
        if (!event.getTagName().equals("event") || !event.getAttribute("uid").equals(uid) ||
                !event.getAttribute("type").equals(type) || !event.getAttribute("how").equals("m-s")) throw new IOException("Synthetic CoT identity/marking validation failed");
        Element contact = (Element) event.getElementsByTagName("contact").item(0);
        Element point = (Element) event.getElementsByTagName("point").item(0);
        Element extension = (Element) event.getElementsByTagName("ouitak").item(0);
        if (contact == null || !contact.getAttribute("callsign").equals(callsign) || point == null || extension == null ||
                !extension.getAttribute("synthetic").equals("true") || !extension.getAttribute("verified").equals("false")) {
            throw new IOException("Synthetic aircraft fixture validation failed");
        }
        Instant wireTime = Instant.parse(event.getAttribute("time"));
        Instant wireStale = Instant.parse(event.getAttribute("stale"));
        if (!wireTime.equals(expectedTime) || wireStale.getEpochSecond() - wireTime.getEpochSecond() != staleSeconds) {
            throw new IOException("CoT event time or stale interval validation failed");
        }
        double latitude = Double.parseDouble(point.getAttribute("lat"));
        double longitude = Double.parseDouble(point.getAttribute("lon"));
        Element track = (Element) event.getElementsByTagName("track").item(0);
        if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 ||
                !Double.isFinite(Double.parseDouble(point.getAttribute("hae"))) || track == null ||
                !Double.isFinite(Double.parseDouble(track.getAttribute("speed"))) ||
                !Double.isFinite(Double.parseDouble(track.getAttribute("course")))) {
            throw new IOException("Synthetic CoT coordinate validation failed");
        }
        if (!xml.endsWith("</event>") || xml.endsWith("\n") || xml.contains("\r")) {
            throw new IOException("CoT event has invalid stream framing");
        }
    }

    private static String safeFailure(Exception error) {
        if (error instanceof SSLHandshakeException) return "TLS handshake/client-certificate authentication failed; no CoT was sent";
        if (error instanceof SSLPeerUnverifiedException) return "server certificate or hostname validation failed; no CoT was sent";
        if (error instanceof ConnectException) return "TCP connection failed; no CoT was sent";
        if (error instanceof java.net.SocketTimeoutException) return "connection or peer read timed out";
        return "test stopped at " + error.getClass().getSimpleName() + "; sensitive exception details suppressed";
    }

    private static void log(String message) {
        System.out.println(Instant.now().truncatedTo(ChronoUnit.MILLIS) + " " + message);
    }

    private static final class Config {
        final String connectAddress;
        final String tlsHostname;
        final int port;
        final Path clientP12;
        final Path passwordFile;
        final Path caFile;
        final Path fixture;
        final int updates;
        final long intervalMillis;
        final int staleSeconds;
        final boolean preflight;
        final Mode mode;

        private Config(String connectAddress, String tlsHostname, int port, Path clientP12, Path passwordFile,
                       Path caFile, Path fixture, int updates, long intervalMillis, int staleSeconds, boolean preflight, Mode mode) {
            this.connectAddress = connectAddress;
            this.tlsHostname = tlsHostname;
            this.port = port;
            this.clientP12 = clientP12;
            this.passwordFile = passwordFile;
            this.caFile = caFile;
            this.fixture = fixture;
            this.updates = updates;
            this.intervalMillis = intervalMillis;
            this.staleSeconds = staleSeconds;
            this.preflight = preflight;
            this.mode = mode;
        }

        static Config parse(String[] args) {
            String address = DEFAULT_CONNECT;
            String hostname = DEFAULT_HOSTNAME;
            int port = System.getenv("DRONK_TAK_PORT") == null ? DEFAULT_PORT : Integer.parseInt(System.getenv("DRONK_TAK_PORT"));
            Path p12 = System.getenv("DRONK_TAK_CLIENT_P12") == null ? null : Path.of(System.getenv("DRONK_TAK_CLIENT_P12"));
            Path password = System.getenv("DRONK_TAK_PASSWORD_FILE") == null ? null : Path.of(System.getenv("DRONK_TAK_PASSWORD_FILE"));
            Path ca = System.getenv("DRONK_TAK_CA_FILE") == null ? null : Path.of(System.getenv("DRONK_TAK_CA_FILE"));
            Path fixture = Path.of("tools/milestone-0.6/fixtures/synthetic-aircraft.xml.template");
            int updates = 5, stale = 12;
            long intervalMillis = 2_000;
            boolean preflight = false;
            Mode mode = Mode.LEGACY;
            for (int i = 0; i < args.length; i++) {
                String option = args[i];
                if (option.equals("--preflight")) { preflight = true; continue; }
                if (option.equals("--icons")) { mode = Mode.ICONS; continue; }
                if (option.equals("--flight")) { mode = Mode.FLIGHT; continue; }
                if (i + 1 >= args.length) throw new IllegalArgumentException("Missing value for " + option);
                String value = args[++i];
                switch (option) {
                    case "--connect" -> address = value;
                    case "--hostname" -> hostname = value;
                    case "--port" -> port = Integer.parseInt(value);
                    case "--client-p12" -> p12 = Path.of(value);
                    case "--password-file" -> password = Path.of(value);
                    case "--ca-file" -> ca = Path.of(value);
                    case "--fixture" -> fixture = Path.of(value);
                    case "--updates" -> updates = Integer.parseInt(value);
                    case "--interval-seconds" -> intervalMillis = Long.parseLong(value) * 1_000;
                    case "--stale-seconds" -> stale = Integer.parseInt(value);
                    default -> throw new IllegalArgumentException("Unknown option " + option);
                }
            }
            if (!"LIVE".equals(System.getenv("DRONK_MODE")))
                throw new IllegalArgumentException("Manual network diagnostic requires explicit LIVE mode");
            if (hostname == null || hostname.isBlank()) throw new IllegalArgumentException("Explicit TLS hostname required");
            if (address == null || address.isBlank()) address = hostname;
            if (p12 == null || password == null || ca == null) {
                throw new IllegalArgumentException("Set --client-p12, --password-file and --ca-file to local protected files");
            }
            if (port < 1 || port > 65535 || updates < 1 || updates > 20 || intervalMillis < 250 ||
                    intervalMillis > 60_000 || stale < 5 || stale > 120) {
                throw new IllegalArgumentException("Port/update/timing values are outside safe test limits");
            }
            return new Config(address, hostname, port, p12, password, ca, fixture, updates, intervalMillis, stale, preflight, mode);
        }
    }

    private enum Mode { LEGACY, ICONS, FLIGHT }
}
