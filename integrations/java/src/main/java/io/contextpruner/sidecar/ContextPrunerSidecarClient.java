package io.contextpruner.sidecar;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Zero-dependency Java 11+ client for the Context-Pruner lifecycle Sidecar.
 *
 * <p>The host owns the authoritative full history. {@link #beforeModel} returns
 * a temporary model view and must never replace that full history.</p>
 */
public final class ContextPrunerSidecarClient implements AutoCloseable {
    private final URI baseUri;
    private final String authToken;
    private final Duration timeout;
    private final HttpClient http;

    public ContextPrunerSidecarClient(String baseUrl, String authToken, Duration timeout) {
        if (baseUrl == null || baseUrl.trim().isEmpty()) {
            throw new IllegalArgumentException("baseUrl is required");
        }
        URI parsed = URI.create(baseUrl.trim());
        if (!("http".equalsIgnoreCase(parsed.getScheme()) || "https".equalsIgnoreCase(parsed.getScheme()))) {
            throw new IllegalArgumentException("baseUrl must use http or https");
        }
        String normalized = parsed.toString();
        this.baseUri = URI.create(normalized.endsWith("/") ? normalized.substring(0, normalized.length() - 1) : normalized);
        this.authToken = authToken == null ? "" : authToken;
        this.timeout = timeout == null ? Duration.ofSeconds(30) : timeout;
        if (this.timeout.isZero() || this.timeout.isNegative()) {
            throw new IllegalArgumentException("timeout must be positive");
        }
        this.http = HttpClient.newBuilder().connectTimeout(this.timeout).build();
    }

    public ContextPrunerSidecarClient(String baseUrl, String authToken) {
        this(baseUrl, authToken, Duration.ofSeconds(30));
    }

    public Map<String, Object> health() throws IOException, InterruptedException {
        return request("GET", "/health", null, false);
    }

    public Map<String, Object> capabilities() throws IOException, InterruptedException {
        return request("GET", "/v1/capabilities", null, true);
    }

    public Map<String, Object> beforeModel(String sessionId, Map<String, Object> payload)
            throws IOException, InterruptedException {
        return sessionRequest(sessionId, "before-model", "POST", payload);
    }

    public Map<String, Object> afterModel(String sessionId, Object output)
            throws IOException, InterruptedException {
        return sessionRequest(sessionId, "after-model", "POST", map("output", output));
    }

    public Map<String, Object> afterTool(String sessionId, Object output)
            throws IOException, InterruptedException {
        return sessionRequest(sessionId, "after-tool", "POST", map("output", output));
    }

    public Map<String, Object> onError(String sessionId, Object error, String query, boolean recover)
            throws IOException, InterruptedException {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("error", String.valueOf(error));
        payload.put("query", query == null ? "" : query);
        payload.put("recover", recover);
        return sessionRequest(sessionId, "error", "POST", payload);
    }

    public Map<String, Object> finalizeSession(String sessionId, Map<String, Object> metadata)
            throws IOException, InterruptedException {
        return sessionRequest(sessionId, "finalize", "POST", map("metadata", metadata == null ? Collections.emptyMap() : metadata));
    }

    public Map<String, Object> getState(String sessionId) throws IOException, InterruptedException {
        return sessionRequest(sessionId, "state", "GET", null);
    }

    public Map<String, Object> restoreState(
            String sessionId,
            Map<String, Object> lifecycleState,
            String taskState,
            Map<String, Object> config
    ) throws IOException, InterruptedException {
        Map<String, Object> payload = new LinkedHashMap<>();
        if (config != null) payload.putAll(config);
        payload.put("lifecycle_state", lifecycleState);
        payload.put("task_state", taskState == null ? "" : taskState);
        return sessionRequest(sessionId, "state", "PUT", payload);
    }

    public Map<String, Object> deleteSession(String sessionId) throws IOException, InterruptedException {
        return sessionRequest(sessionId, "", "DELETE", null);
    }

    private Map<String, Object> sessionRequest(
            String sessionId, String suffix, String method, Map<String, Object> payload
    ) throws IOException, InterruptedException {
        if (sessionId == null || sessionId.trim().isEmpty()) {
            throw new IllegalArgumentException("sessionId is required");
        }
        String encoded = URLEncoder.encode(sessionId.trim(), StandardCharsets.UTF_8).replace("+", "%20");
        String tail = suffix == null || suffix.isEmpty() ? "" : "/" + suffix;
        return request(method, "/v1/sessions/" + encoded + tail, payload, true);
    }

    private Map<String, Object> request(
            String method, String path, Map<String, Object> payload, boolean authenticated
    ) throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(baseUri.resolve(path))
                .timeout(timeout)
                .header("Accept", "application/json");
        if (authenticated && !authToken.isEmpty()) {
            builder.header("Authorization", "Bearer " + authToken);
        }
        if (payload == null) {
            builder.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            builder.header("Content-Type", "application/json")
                    .method(method, HttpRequest.BodyPublishers.ofString(JsonCodec.stringify(payload), StandardCharsets.UTF_8));
        }
        HttpResponse<String> response = http.send(
                builder.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)
        );
        Map<String, Object> data;
        try {
            data = response.body() == null || response.body().isEmpty()
                    ? new LinkedHashMap<>()
                    : JsonCodec.parseObject(response.body());
        } catch (IllegalArgumentException error) {
            throw new SidecarException("Sidecar returned invalid JSON", response.statusCode(), "invalid_json", response.body());
        }
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            Object errorValue = data.get("error");
            Map<?, ?> remote = errorValue instanceof Map ? (Map<?, ?>) errorValue : Collections.emptyMap();
            Object remoteMessage = remote.containsKey("message")
                    ? remote.get("message")
                    : "Sidecar request failed with HTTP " + response.statusCode();
            Object remoteCode = remote.containsKey("code") ? remote.get("code") : "";
            String message = String.valueOf(remoteMessage);
            String code = String.valueOf(remoteCode);
            throw new SidecarException(message, response.statusCode(), code, data);
        }
        return data;
    }

    private static Map<String, Object> map(String key, Object value) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put(key, value);
        return result;
    }

    @Override
    public void close() {
        // java.net.http.HttpClient does not own a closeable resource in Java 11.
    }

    public static final class SidecarException extends IOException {
        private static final long serialVersionUID = 1L;
        private final int status;
        private final String code;
        private final Object details;

        public SidecarException(String message, int status, String code, Object details) {
            super(message);
            this.status = status;
            this.code = code == null ? "" : code;
            this.details = details;
        }

        public int getStatus() { return status; }
        public String getCode() { return code; }
        public Object getDetails() { return details; }
    }
}
