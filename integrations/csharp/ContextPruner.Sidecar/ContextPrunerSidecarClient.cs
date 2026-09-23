using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ContextPruner.Sidecar;

/// <summary>
/// Dependency-free .NET 8 client for the Context-Pruner HTTP lifecycle Sidecar.
/// The host keeps its authoritative full history; BeforeModelAsync only returns
/// the temporary view that should be sent to the model for one turn.
/// </summary>
public sealed class ContextPrunerSidecarClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly Uri _baseUri;
    private readonly string _authToken;
    private readonly HttpClient _http;
    private readonly bool _ownsHttpClient;

    public ContextPrunerSidecarClient(
        string baseUrl,
        string? authToken = null,
        TimeSpan? timeout = null,
        HttpClient? httpClient = null)
    {
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var parsed) ||
            (parsed.Scheme != Uri.UriSchemeHttp && parsed.Scheme != Uri.UriSchemeHttps))
        {
            throw new ArgumentException("baseUrl must be an absolute HTTP(S) URL", nameof(baseUrl));
        }

        var effectiveTimeout = timeout ?? TimeSpan.FromSeconds(30);
        if (effectiveTimeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(timeout), "timeout must be positive");
        }

        _baseUri = new Uri(parsed.ToString().TrimEnd('/') + "/", UriKind.Absolute);
        _authToken = authToken ?? string.Empty;
        _http = httpClient ?? new HttpClient();
        _ownsHttpClient = httpClient is null;
        _http.Timeout = effectiveTimeout;
    }

    public Task<JsonObject> HealthAsync(CancellationToken cancellationToken = default) =>
        RequestAsync(HttpMethod.Get, "health", null, authenticated: false, cancellationToken);

    public Task<JsonObject> CapabilitiesAsync(CancellationToken cancellationToken = default) =>
        RequestAsync(HttpMethod.Get, "v1/capabilities", null, authenticated: true, cancellationToken);

    public Task<JsonObject> BeforeModelAsync(
        string sessionId, JsonObject payload, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(sessionId, "before-model", HttpMethod.Post, payload, cancellationToken);

    public Task<JsonObject> AfterModelAsync(
        string sessionId, JsonNode? output, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(
            sessionId,
            "after-model",
            HttpMethod.Post,
            new JsonObject { ["output"] = output?.DeepClone() },
            cancellationToken);

    public Task<JsonObject> AfterToolAsync(
        string sessionId, JsonNode? output, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(
            sessionId,
            "after-tool",
            HttpMethod.Post,
            new JsonObject { ["output"] = output?.DeepClone() },
            cancellationToken);

    public Task<JsonObject> OnErrorAsync(
        string sessionId,
        object error,
        string? query = null,
        bool recover = true,
        CancellationToken cancellationToken = default) =>
        SessionRequestAsync(
            sessionId,
            "error",
            HttpMethod.Post,
            new JsonObject
            {
                ["error"] = Convert.ToString(error, System.Globalization.CultureInfo.InvariantCulture),
                ["query"] = query ?? string.Empty,
                ["recover"] = recover,
            },
            cancellationToken);

    public Task<JsonObject> FinalizeAsync(
        string sessionId, JsonObject? metadata = null, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(
            sessionId,
            "finalize",
            HttpMethod.Post,
            new JsonObject { ["metadata"] = metadata?.DeepClone() ?? new JsonObject() },
            cancellationToken);

    public Task<JsonObject> GetStateAsync(
        string sessionId, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(sessionId, "state", HttpMethod.Get, null, cancellationToken);

    public Task<JsonObject> RestoreStateAsync(
        string sessionId,
        JsonObject lifecycleState,
        string? taskState = null,
        JsonObject? config = null,
        CancellationToken cancellationToken = default)
    {
        var payload = config?.DeepClone().AsObject() ?? new JsonObject();
        payload["lifecycle_state"] = lifecycleState.DeepClone();
        payload["task_state"] = taskState ?? string.Empty;
        return SessionRequestAsync(sessionId, "state", HttpMethod.Put, payload, cancellationToken);
    }

    public Task<JsonObject> DeleteSessionAsync(
        string sessionId, CancellationToken cancellationToken = default) =>
        SessionRequestAsync(sessionId, string.Empty, HttpMethod.Delete, null, cancellationToken);

    private Task<JsonObject> SessionRequestAsync(
        string sessionId,
        string suffix,
        HttpMethod method,
        JsonObject? payload,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(sessionId))
        {
            throw new ArgumentException("sessionId is required", nameof(sessionId));
        }

        var path = $"v1/sessions/{Uri.EscapeDataString(sessionId.Trim())}";
        if (!string.IsNullOrEmpty(suffix))
        {
            path += "/" + suffix;
        }

        return RequestAsync(method, path, payload, authenticated: true, cancellationToken);
    }

    private async Task<JsonObject> RequestAsync(
        HttpMethod method,
        string path,
        JsonObject? payload,
        bool authenticated,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, new Uri(_baseUri, path));
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (authenticated && !string.IsNullOrEmpty(_authToken))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _authToken);
        }

        if (payload is not null)
        {
            request.Content = new StringContent(
                payload.ToJsonString(JsonOptions), Encoding.UTF8, "application/json");
        }

        using var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        JsonObject data;
        try
        {
            data = string.IsNullOrWhiteSpace(body)
                ? new JsonObject()
                : JsonNode.Parse(body)?.AsObject() ?? new JsonObject();
        }
        catch (JsonException error)
        {
            throw new InvalidOperationException(
                $"Sidecar returned invalid JSON (HTTP {(int)response.StatusCode})", error);
        }

        if (!response.IsSuccessStatusCode)
        {
            var detail = data["error"]?.GetValue<string>() ?? body;
            throw new HttpRequestException(
                $"Sidecar HTTP {(int)response.StatusCode}: {detail}",
                null,
                response.StatusCode);
        }

        return data;
    }

    public void Dispose()
    {
        if (_ownsHttpClient)
        {
            _http.Dispose();
        }
    }
}
