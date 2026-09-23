package io.contextpruner.example;

import io.contextpruner.sidecar.ContextPrunerSidecarClient;
import io.contextpruner.sidecar.JsonCodec;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Real Java custom-Agent boundary validation for the lifecycle Sidecar. */
public final class JavaCustomAgentExperiment {
    private JavaCustomAgentExperiment() {}

    private static final List<Task> TASKS = Arrays.asList(
            new Task(
                    "incident_decision",
                    "Apply the current incident rule and return the one-line decision.",
                    "Use only the latest verified dossier. Return only the requested one-line contract.",
                    "CURRENT DOSSIER: Incident INC-771 affects service Meridian-3. Verified rollback_trigger=true and customer_impact=confirmed. Rule: when both fields are true, action is ROLLBACK; otherwise action is MONITOR. Return exactly: DECISION incident=INC-771 action=<action>",
                    Arrays.asList("INC-771", "ROLLBACK"),
                    "DECISION incident=INC-771 action=ROLLBACK"
            ),
            new Task(
                    "release_gate",
                    "Evaluate the current release gates and return the one-line status.",
                    "Use only the latest verified release record. Return only the requested one-line contract.",
                    "CURRENT RELEASE: Atlas-16 has unit_tests=passed, integration_tests=passed, and security_gate=failed. Rule: every required gate must pass; any failed required gate makes the release BLOCKED. Return exactly: RELEASE name=Atlas-16 status=<status>",
                    Arrays.asList("Atlas-16", "BLOCKED"),
                    "RELEASE name=Atlas-16 status=BLOCKED"
            ),
            new Task(
                    "migration_plan",
                    "Select the approved current migration target and window.",
                    "Use only the latest approved migration record. Return only the requested one-line contract.",
                    "CURRENT MIGRATION: Customer Lyra approved region=eu-central and window=02:20 UTC. The approved record supersedes all draft alternatives. Return exactly: MIGRATION customer=Lyra region=<region> window=<window>",
                    Arrays.asList("Lyra", "eu-central", "02:20 UTC"),
                    "MIGRATION customer=Lyra region=eu-central window=02:20 UTC"
            )
    );

    public static void main(String[] arguments) throws Exception {
        Options options = Options.parse(arguments);
        String sidecarUrl = requiredEnvironment("CONTEXT_PRUNER_BASE_URL");
        String sidecarToken = environment("CONTEXT_PRUNER_AUTH_TOKEN");
        List<Object> rows = new ArrayList<>();
        Map<String, Object> probe;
        try (ContextPrunerSidecarClient client = new ContextPrunerSidecarClient(
                sidecarUrl, sidecarToken, Duration.ofSeconds(60))) {
            for (int repeat = 0; repeat < options.repeats; repeat++) {
                for (Task task : TASKS) {
                    for (String method : Arrays.asList("none", "pruner_v1")) {
                        rows.add(runCase(client, task, repeat, method, options));
                    }
                }
            }
            probe = runLifecycleProbe(client);
        }
        System.out.println(JsonCodec.stringify(map(
                "mode", options.mode,
                "rows", rows,
                "lifecycle_probe", probe
        )));
    }

    private static Map<String, Object> runCase(
            ContextPrunerSidecarClient client,
            Task task,
            int repeat,
            String method,
            Options options
    ) throws Exception {
        String sessionId = "java:" + task.id + ":r" + repeat + ":" + method;
        List<Object> fullHistory = buildHistory(task);
        Map<String, Object> before = client.beforeModel(sessionId, map(
                "messages", fullHistory,
                "task_state", task.taskState,
                "method", method,
                "budget", map("soft", 900, "hard", 1200, "target", 650),
                "reserved_tokens", 180
        ));
        @SuppressWarnings("unchecked")
        List<Object> modelView = (List<Object>) before.get("messages");
        ModelResult completion = options.mode.equals("mock")
                ? new ModelResult(task.mockOutput, 0, 0)
                : callModel(modelView, options);
        client.afterModel(sessionId, map("role", "assistant", "content", completion.output));
        Map<String, Object> finalized = client.finalizeSession(sessionId, map(
                "host", "custom-java-agent", "mode", options.mode
        ));
        String normalized = completion.output.toLowerCase(Locale.ROOT);
        List<Object> missing = new ArrayList<>();
        for (String term : task.expected) {
            if (!normalized.contains(term.toLowerCase(Locale.ROOT))) missing.add(term);
        }
        Map<String, Object> metrics = object(finalized.get("metrics"));
        Map<String, Object> lifecycle = object(finalized.get("lifecycle_state"));
        Map<String, Object> plugin = object(lifecycle.get("plugin"));
        return map(
                "task_id", task.id,
                "repeat", repeat,
                "method", method,
                "session_id", sessionId,
                "success", !completion.output.isEmpty() && missing.isEmpty(),
                "missing_terms", missing,
                "model_output", completion.output,
                "prompt_tokens", completion.promptTokens,
                "completion_tokens", completion.completionTokens,
                "full_context_tokens", number(metrics.get("last_full_context_tokens")),
                "served_context_tokens", number(metrics.get("last_served_context_tokens")),
                "model_input_budget_violation_count", number(metrics.get("model_input_budget_violation_count")),
                "finalized", Boolean.TRUE.equals(plugin.get("finalized"))
        );
    }

    private static List<Object> buildHistory(Task task) {
        List<Object> messages = new ArrayList<>();
        messages.add(map("role", "system", "content", task.system));
        for (int index = 0; index < 14; index++) {
            messages.add(map(
                    "role", "assistant",
                    "content", repeat("OBSOLETE JAVA DRAFT " + index + ": superseded identifiers, regions, windows, and actions. ", 3)
            ));
            messages.add(map(
                    "role", "user",
                    "content", repeat("ARCHIVED JAVA REQUEST " + index + ": compare an abandoned option that is no longer authorized. ", 3)
            ));
        }
        messages.add(map("role", "user", "content", task.current));
        return messages;
    }

    private static ModelResult callModel(List<Object> messages, Options options) throws Exception {
        String key = requiredEnvironment("MODEL_API_KEY");
        HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(60)).build();
        Map<String, Object> body = map(
                "model", options.model,
                "messages", messages,
                "temperature", 0,
                "max_tokens", options.maxOutputTokens,
                "thinking", map("type", "disabled")
        );
        Exception lastError = null;
        for (int attempt = 0; attempt <= options.maxApiRetries; attempt++) {
            try {
                HttpRequest request = HttpRequest.newBuilder(
                                URI.create(trimSlash(options.modelBaseUrl) + "/chat/completions"))
                        .timeout(Duration.ofSeconds(90))
                        .header("Authorization", "Bearer " + key)
                        .header("Content-Type", "application/json")
                        .header("Accept", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(JsonCodec.stringify(body), StandardCharsets.UTF_8))
                        .build();
                HttpResponse<String> response = http.send(
                        request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
                if (response.statusCode() < 200 || response.statusCode() >= 300) {
                    boolean retryable = response.statusCode() == 429 || response.statusCode() >= 500;
                    if (!retryable || attempt >= options.maxApiRetries) {
                        throw new IllegalStateException("Model HTTP " + response.statusCode() + ": " + response.body());
                    }
                    Thread.sleep(500L * (1L << attempt));
                    continue;
                }
                Map<String, Object> payload = JsonCodec.parseObject(response.body());
                List<?> choices = (List<?>) payload.get("choices");
                Map<String, Object> choice = object(choices.get(0));
                Map<String, Object> message = object(choice.get("message"));
                Map<String, Object> usage = object(payload.get("usage"));
                String output = String.valueOf(message.getOrDefault("content", "")).trim();
                if (output.isEmpty()) throw new IllegalStateException("Model returned empty content");
                return new ModelResult(output, number(usage.get("prompt_tokens")), number(usage.get("completion_tokens")));
            } catch (Exception error) {
                lastError = error;
                if (attempt >= options.maxApiRetries) break;
                Thread.sleep(500L * (1L << attempt));
            }
        }
        throw lastError;
    }

    private static Map<String, Object> runLifecycleProbe(ContextPrunerSidecarClient client) throws Exception {
        String sourceId = "java:lifecycle:source";
        String restoredId = "java:lifecycle:restored";
        List<Object> messages = new ArrayList<>();
        messages.add(map("role", "system", "content", "Hard constraint: preserve Java-Omega-42."));
        for (int index = 0; index < 20; index++) {
            String marker = String.format("%02d", index);
            messages.add(map("role", "assistant", "content", repeat("Archived Java evidence ARCHIVE-JAVA-" + marker + ": obsolete branch. ", 3)));
            messages.add(map("role", "user", "content", repeat("Old Java request " + marker + ": inspect ARCHIVE-JAVA-" + marker + ". ", 3)));
        }
        messages.add(map("role", "user", "content", "Use fresh tool evidence for Java-Omega-42."));
        Map<String, Object> health = client.health();
        Map<String, Object> capabilities = client.capabilities();
        client.beforeModel(sourceId, map(
                "messages", messages,
                "task_state", "Preserve Java-Omega-42 and fresh tool evidence.",
                "method", "pruner_v1",
                "budget", map("soft", 700, "hard", 900, "target", 520),
                "reserved_tokens", 120
        ));
        client.afterTool(sourceId, map(
                "role", "user",
                "content", "VERIFIED TOOL RESULT [lookup_ticket]: JAVA-TOOL-908 severity=critical project=Java-Omega-42"
        ));
        client.afterModel(sourceId, map("role", "assistant", "content", "Recorded JAVA-TOOL-908 for Java-Omega-42."));
        Map<String, Object> exported = client.getState(sourceId);
        client.restoreState(restoredId, object(exported.get("lifecycle_state")), "Restored Java lifecycle", Collections.emptyMap());
        Map<String, Object> recovered = client.onError(restoredId, "synthetic Java retry", "ARCHIVE-JAVA-07", true);
        Map<String, Object> finalized = client.finalizeSession(restoredId, map(
                "host", "custom-java-agent", "validation", "full-client-lifecycle"
        ));
        String serialized = JsonCodec.stringify(finalized.get("lifecycle_state"));
        Map<String, Object> sourceDelete = client.deleteSession(sourceId);
        Map<String, Object> restoredDelete = client.deleteSession(restoredId);
        Map<String, Object> metrics = object(finalized.get("metrics"));
        boolean success = "ok".equals(health.get("status"))
                && capabilities.get("routes") instanceof Map
                && serialized.contains("\"finalized\":true")
                && number(metrics.get("lifecycle_recovery_count")) >= 1
                && serialized.contains("JAVA-TOOL-908")
                && serialized.contains("Java-Omega-42")
                && Boolean.TRUE.equals(sourceDelete.get("deleted"))
                && Boolean.TRUE.equals(restoredDelete.get("deleted"));
        return map(
                "success", success,
                "health_ok", "ok".equals(health.get("status")),
                "capabilities_ok", capabilities.get("routes") instanceof Map,
                "finalized", serialized.contains("\"finalized\":true"),
                "recovery_count", number(metrics.get("lifecycle_recovery_count")),
                "tool_fact_preserved", serialized.contains("JAVA-TOOL-908"),
                "constraint_preserved", serialized.contains("Java-Omega-42"),
                "sessions_deleted", Boolean.TRUE.equals(sourceDelete.get("deleted")) && Boolean.TRUE.equals(restoredDelete.get("deleted")),
                "recovery_view_contains_query", JsonCodec.stringify(recovered.get("messages")).contains("ARCHIVE-JAVA-07")
        );
    }

    private static String repeat(String value, int times) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < times; index++) result.append(value);
        return result.toString();
    }

    private static String trimSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static String environment(String name) {
        String value = System.getenv(name);
        return value == null ? "" : value;
    }

    private static String requiredEnvironment(String name) {
        String value = environment(name);
        if (value.isEmpty()) throw new IllegalStateException(name + " is required");
        return value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<>();
    }

    private static int number(Object value) {
        return value instanceof Number ? ((Number) value).intValue() : 0;
    }

    private static Map<String, Object> map(Object... pairs) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < pairs.length; index += 2) {
            result.put(String.valueOf(pairs[index]), pairs[index + 1]);
        }
        return result;
    }

    private static final class Task {
        private final String id;
        private final String taskState;
        private final String system;
        private final String current;
        private final List<String> expected;
        private final String mockOutput;

        private Task(String id, String taskState, String system, String current, List<String> expected, String mockOutput) {
            this.id = id;
            this.taskState = taskState;
            this.system = system;
            this.current = current;
            this.expected = expected;
            this.mockOutput = mockOutput;
        }
    }

    private static final class ModelResult {
        private final String output;
        private final int promptTokens;
        private final int completionTokens;

        private ModelResult(String output, int promptTokens, int completionTokens) {
            this.output = output;
            this.promptTokens = promptTokens;
            this.completionTokens = completionTokens;
        }
    }

    private static final class Options {
        private String mode = "mock";
        private int repeats = 1;
        private String model = "deepseek-v4-flash";
        private String modelBaseUrl = "https://api.deepseek.com";
        private int maxOutputTokens = 160;
        private int maxApiRetries = 2;

        private static Options parse(String[] arguments) {
            Options options = new Options();
            for (int index = 0; index < arguments.length; index += 2) {
                if (index + 1 >= arguments.length) throw new IllegalArgumentException("Missing value for " + arguments[index]);
                String name = arguments[index];
                String value = arguments[index + 1];
                if ("--mode".equals(name)) options.mode = value;
                else if ("--repeats".equals(name)) options.repeats = Integer.parseInt(value);
                else if ("--model".equals(name)) options.model = value;
                else if ("--model-base-url".equals(name)) options.modelBaseUrl = value;
                else if ("--max-output-tokens".equals(name)) options.maxOutputTokens = Integer.parseInt(value);
                else if ("--max-api-retries".equals(name)) options.maxApiRetries = Integer.parseInt(value);
                else throw new IllegalArgumentException("Unknown argument: " + name);
            }
            if (!("mock".equals(options.mode) || "api".equals(options.mode))) {
                throw new IllegalArgumentException("--mode must be mock or api");
            }
            if (options.repeats <= 0 || options.maxApiRetries < 0) {
                throw new IllegalArgumentException("repeats must be positive and retries non-negative");
            }
            return options;
        }
    }
}
