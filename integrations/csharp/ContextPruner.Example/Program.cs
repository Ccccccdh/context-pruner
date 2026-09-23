using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using ContextPruner.Sidecar;

namespace ContextPruner.Example;

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly string[] Methods = ["none", "pruner_v1"];
    private static readonly TaskCase[] Tasks =
    [
        new(
            "incident_decision",
            "Apply the current incident rule and return the one-line decision.",
            "Use only the latest verified dossier. Return only the requested one-line contract.",
            "CURRENT DOSSIER: Incident INC-884 affects service Polaris-7. Verified rollback_trigger=true and customer_impact=confirmed. Rule: when both fields are true, action is ROLLBACK; otherwise action is MONITOR. Return exactly: DECISION incident=INC-884 action=<action>",
            ["INC-884", "ROLLBACK"],
            "DECISION incident=INC-884 action=ROLLBACK"),
        new(
            "release_gate",
            "Evaluate the current release gates and return the one-line status.",
            "Use only the latest verified release record. Return only the requested one-line contract.",
            "CURRENT RELEASE: Vega-21 has unit_tests=passed, integration_tests=passed, and security_gate=failed. Rule: every required gate must pass; any failed required gate makes the release BLOCKED. Return exactly: RELEASE name=Vega-21 status=<status>",
            ["Vega-21", "BLOCKED"],
            "RELEASE name=Vega-21 status=BLOCKED"),
        new(
            "migration_plan",
            "Select the approved current migration target and window.",
            "Use only the latest approved migration record. Return only the requested one-line contract.",
            "CURRENT MIGRATION: Customer Cygnus approved region=ap-southeast and window=03:40 UTC. The approved record supersedes all draft alternatives. Return exactly: MIGRATION customer=Cygnus region=<region> window=<window>",
            ["Cygnus", "ap-southeast", "03:40 UTC"],
            "MIGRATION customer=Cygnus region=ap-southeast window=03:40 UTC"),
    ];

    public static async Task<int> Main(string[] arguments)
    {
        var options = Options.Parse(arguments);
        var sidecarUrl = RequiredEnvironment("CONTEXT_PRUNER_BASE_URL");
        var sidecarToken = Environment.GetEnvironmentVariable("CONTEXT_PRUNER_AUTH_TOKEN");
        var rows = new JsonArray();

        using var client = new ContextPrunerSidecarClient(
            sidecarUrl, sidecarToken, TimeSpan.FromSeconds(60));
        for (var repeat = 0; repeat < options.Repeats; repeat++)
        {
            foreach (var task in Tasks)
            {
                foreach (var method in Methods)
                {
                    rows.Add(await RunCaseAsync(client, task, repeat, method, options));
                }
            }
        }

        var probe = await RunLifecycleProbeAsync(client);
        var result = new JsonObject
        {
            ["mode"] = options.Mode,
            ["rows"] = rows,
            ["lifecycle_probe"] = probe,
        };
        Console.WriteLine(result.ToJsonString(JsonOptions));
        return 0;
    }

    private static async Task<JsonObject> RunCaseAsync(
        ContextPrunerSidecarClient client,
        TaskCase task,
        int repeat,
        string method,
        Options options)
    {
        var sessionId = $"csharp:{task.Id}:r{repeat}:{method}";
        var before = await client.BeforeModelAsync(sessionId, new JsonObject
        {
            ["messages"] = BuildHistory(task),
            ["task_state"] = task.TaskState,
            ["method"] = method,
            ["budget"] = new JsonObject { ["soft"] = 900, ["hard"] = 1200, ["target"] = 650 },
            ["reserved_tokens"] = 180,
        });
        var modelView = before["messages"]?.AsArray()
            ?? throw new InvalidOperationException("Sidecar response is missing messages");
        var completion = options.Mode == "mock"
            ? new ModelResult(task.MockOutput, 0, 0)
            : await CallModelAsync(modelView, options);

        await client.AfterModelAsync(sessionId, new JsonObject
        {
            ["role"] = "assistant",
            ["content"] = completion.Output,
        });
        var finalized = await client.FinalizeAsync(sessionId, new JsonObject
        {
            ["host"] = "custom-csharp-agent",
            ["mode"] = options.Mode,
        });
        var missing = new JsonArray();
        foreach (var expected in task.Expected)
        {
            if (!completion.Output.Contains(expected, StringComparison.OrdinalIgnoreCase))
            {
                missing.Add(expected);
            }
        }

        var metrics = Object(finalized["metrics"]);
        var lifecycle = Object(finalized["lifecycle_state"]);
        var plugin = Object(lifecycle["plugin"]);
        return new JsonObject
        {
            ["task_id"] = task.Id,
            ["repeat"] = repeat,
            ["method"] = method,
            ["session_id"] = sessionId,
            ["success"] = completion.Output.Length > 0 && missing.Count == 0,
            ["missing_terms"] = missing,
            ["model_output"] = completion.Output,
            ["prompt_tokens"] = completion.PromptTokens,
            ["completion_tokens"] = completion.CompletionTokens,
            ["full_context_tokens"] = Number(metrics["last_full_context_tokens"]),
            ["served_context_tokens"] = Number(metrics["last_served_context_tokens"]),
            ["model_input_budget_violation_count"] = Number(metrics["model_input_budget_violation_count"]),
            ["finalized"] = Boolean(plugin["finalized"]),
        };
    }

    private static JsonArray BuildHistory(TaskCase task)
    {
        var messages = new JsonArray
        {
            new JsonObject { ["role"] = "system", ["content"] = task.System },
        };
        for (var index = 0; index < 14; index++)
        {
            messages.Add(new JsonObject
            {
                ["role"] = "assistant",
                ["content"] = Repeat($"OBSOLETE CSHARP DRAFT {index}: superseded identifiers, regions, windows, and actions. ", 3),
            });
            messages.Add(new JsonObject
            {
                ["role"] = "user",
                ["content"] = Repeat($"ARCHIVED CSHARP REQUEST {index}: compare an abandoned option that is no longer authorized. ", 3),
            });
        }

        messages.Add(new JsonObject { ["role"] = "user", ["content"] = task.Current });
        return messages;
    }

    private static async Task<ModelResult> CallModelAsync(JsonArray messages, Options options)
    {
        var key = RequiredEnvironment("MODEL_API_KEY");
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(90) };
        var body = new JsonObject
        {
            ["model"] = options.Model,
            ["messages"] = messages.DeepClone(),
            ["temperature"] = 0,
            ["max_tokens"] = options.MaxOutputTokens,
            ["thinking"] = new JsonObject { ["type"] = "disabled" },
        };

        Exception? lastError = null;
        for (var attempt = 0; attempt <= options.MaxApiRetries; attempt++)
        {
            try
            {
                using var request = new HttpRequestMessage(
                    HttpMethod.Post,
                    options.ModelBaseUrl.TrimEnd('/') + "/chat/completions");
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                request.Content = new StringContent(
                    body.ToJsonString(JsonOptions), Encoding.UTF8, "application/json");
                using var response = await http.SendAsync(request);
                var responseBody = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    var retryable = (int)response.StatusCode == 429 || (int)response.StatusCode >= 500;
                    if (!retryable || attempt >= options.MaxApiRetries)
                    {
                        throw new HttpRequestException(
                            $"Model HTTP {(int)response.StatusCode}: {responseBody}", null, response.StatusCode);
                    }

                    await Task.Delay(500 * (1 << attempt));
                    continue;
                }

                var payload = JsonNode.Parse(responseBody)?.AsObject()
                    ?? throw new InvalidOperationException("Model returned invalid JSON");
                var message = payload["choices"]?[0]?["message"]?.AsObject()
                    ?? throw new InvalidOperationException("Model response is missing choices[0].message");
                var output = message["content"]?.GetValue<string>()?.Trim() ?? string.Empty;
                if (output.Length == 0)
                {
                    throw new InvalidOperationException("Model returned empty content");
                }

                var usage = Object(payload["usage"]);
                return new ModelResult(
                    output,
                    Number(usage["prompt_tokens"]),
                    Number(usage["completion_tokens"]));
            }
            catch (Exception error) when (attempt < options.MaxApiRetries)
            {
                lastError = error;
                await Task.Delay(500 * (1 << attempt));
            }
            catch (Exception error)
            {
                lastError = error;
                break;
            }
        }

        throw lastError ?? new InvalidOperationException("Model call failed");
    }

    private static async Task<JsonObject> RunLifecycleProbeAsync(ContextPrunerSidecarClient client)
    {
        const string sourceId = "csharp:lifecycle:source";
        const string restoredId = "csharp:lifecycle:restored";
        var messages = new JsonArray
        {
            new JsonObject { ["role"] = "system", ["content"] = "Hard constraint: preserve Dotnet-Omega-57." },
        };
        for (var index = 0; index < 20; index++)
        {
            var marker = index.ToString("00", System.Globalization.CultureInfo.InvariantCulture);
            messages.Add(new JsonObject
            {
                ["role"] = "assistant",
                ["content"] = Repeat($"Archived C# evidence ARCHIVE-CSHARP-{marker}: obsolete branch. ", 3),
            });
            messages.Add(new JsonObject
            {
                ["role"] = "user",
                ["content"] = Repeat($"Old C# request {marker}: inspect ARCHIVE-CSHARP-{marker}. ", 3),
            });
        }

        messages.Add(new JsonObject
        {
            ["role"] = "user",
            ["content"] = "Use fresh tool evidence for Dotnet-Omega-57.",
        });

        var health = await client.HealthAsync();
        var capabilities = await client.CapabilitiesAsync();
        await client.BeforeModelAsync(sourceId, new JsonObject
        {
            ["messages"] = messages,
            ["task_state"] = "Preserve Dotnet-Omega-57 and fresh tool evidence.",
            ["method"] = "pruner_v1",
            ["budget"] = new JsonObject { ["soft"] = 700, ["hard"] = 900, ["target"] = 520 },
            ["reserved_tokens"] = 120,
        });
        await client.AfterToolAsync(sourceId, new JsonObject
        {
            ["role"] = "user",
            ["content"] = "VERIFIED TOOL RESULT [lookup_ticket]: CSHARP-TOOL-941 severity=critical project=Dotnet-Omega-57",
        });
        await client.AfterModelAsync(sourceId, new JsonObject
        {
            ["role"] = "assistant",
            ["content"] = "Recorded CSHARP-TOOL-941 for Dotnet-Omega-57.",
        });
        var exported = await client.GetStateAsync(sourceId);
        await client.RestoreStateAsync(
            restoredId,
            Object(exported["lifecycle_state"]),
            "Restored C# lifecycle");
        var recovered = await client.OnErrorAsync(
            restoredId, "synthetic C# retry", "ARCHIVE-CSHARP-07", recover: true);
        var finalized = await client.FinalizeAsync(restoredId, new JsonObject
        {
            ["host"] = "custom-csharp-agent",
            ["validation"] = "full-client-lifecycle",
        });
        var serialized = finalized["lifecycle_state"]?.ToJsonString() ?? string.Empty;
        var sourceDelete = await client.DeleteSessionAsync(sourceId);
        var restoredDelete = await client.DeleteSessionAsync(restoredId);
        var metrics = Object(finalized["metrics"]);
        var sessionsDeleted = Boolean(sourceDelete["deleted"]) && Boolean(restoredDelete["deleted"]);
        var success = health["status"]?.GetValue<string>() == "ok"
            && capabilities["routes"] is JsonObject
            && serialized.Contains("\"finalized\":true", StringComparison.Ordinal)
            && Number(metrics["lifecycle_recovery_count"]) >= 1
            && serialized.Contains("CSHARP-TOOL-941", StringComparison.Ordinal)
            && serialized.Contains("Dotnet-Omega-57", StringComparison.Ordinal)
            && sessionsDeleted;

        return new JsonObject
        {
            ["success"] = success,
            ["health_ok"] = health["status"]?.GetValue<string>() == "ok",
            ["capabilities_ok"] = capabilities["routes"] is JsonObject,
            ["finalized"] = serialized.Contains("\"finalized\":true", StringComparison.Ordinal),
            ["recovery_count"] = Number(metrics["lifecycle_recovery_count"]),
            ["tool_fact_preserved"] = serialized.Contains("CSHARP-TOOL-941", StringComparison.Ordinal),
            ["constraint_preserved"] = serialized.Contains("Dotnet-Omega-57", StringComparison.Ordinal),
            ["sessions_deleted"] = sessionsDeleted,
            ["recovery_view_contains_query"] = recovered["messages"]?.ToJsonString()
                .Contains("ARCHIVE-CSHARP-07", StringComparison.Ordinal) == true,
        };
    }

    private static JsonObject Object(JsonNode? value) => value as JsonObject ?? new JsonObject();

    private static int Number(JsonNode? value) => value?.GetValue<int>() ?? 0;

    private static bool Boolean(JsonNode? value) => value?.GetValue<bool>() ?? false;

    private static string Repeat(string value, int times) => string.Concat(Enumerable.Repeat(value, times));

    private static string RequiredEnvironment(string name)
    {
        var value = Environment.GetEnvironmentVariable(name);
        return string.IsNullOrWhiteSpace(value)
            ? throw new InvalidOperationException($"{name} is required")
            : value;
    }

    private sealed record TaskCase(
        string Id,
        string TaskState,
        string System,
        string Current,
        string[] Expected,
        string MockOutput);

    private sealed record ModelResult(string Output, int PromptTokens, int CompletionTokens);

    private sealed class Options
    {
        public string Mode { get; private set; } = "mock";
        public int Repeats { get; private set; } = 1;
        public string Model { get; private set; } = "deepseek-v4-flash";
        public string ModelBaseUrl { get; private set; } = "https://api.deepseek.com";
        public int MaxOutputTokens { get; private set; } = 160;
        public int MaxApiRetries { get; private set; } = 2;

        public static Options Parse(string[] arguments)
        {
            var options = new Options();
            if (arguments.Length % 2 != 0)
            {
                throw new ArgumentException("Every argument must have a value");
            }

            for (var index = 0; index < arguments.Length; index += 2)
            {
                var name = arguments[index];
                var value = arguments[index + 1];
                switch (name)
                {
                    case "--mode": options.Mode = value; break;
                    case "--repeats": options.Repeats = int.Parse(value, System.Globalization.CultureInfo.InvariantCulture); break;
                    case "--model": options.Model = value; break;
                    case "--model-base-url": options.ModelBaseUrl = value; break;
                    case "--max-output-tokens": options.MaxOutputTokens = int.Parse(value, System.Globalization.CultureInfo.InvariantCulture); break;
                    case "--max-api-retries": options.MaxApiRetries = int.Parse(value, System.Globalization.CultureInfo.InvariantCulture); break;
                    default: throw new ArgumentException($"Unknown argument: {name}");
                }
            }

            if (options.Mode is not ("mock" or "api"))
            {
                throw new ArgumentException("--mode must be mock or api");
            }

            if (options.Repeats <= 0 || options.MaxApiRetries < 0)
            {
                throw new ArgumentException("repeats must be positive and retries non-negative");
            }

            return options;
        }
    }
}
