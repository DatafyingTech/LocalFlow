package tech.datafying.localflow

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.io.InterruptedIOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/**
 * The phone side of docs/API.md. Blocking calls; run them off the main thread.
 * Every failure is an [ApiException] whose [ApiException.kind] maps to a dot state and a toast.
 */
class ApiClient(private val settings: Settings) {

    enum class Kind { UNAUTHORIZED, UNAVAILABLE, TIMEOUT, NETWORK, SERVER, BAD_URL }

    class ApiException(val kind: Kind, message: String, val status: Int = 0) : Exception(message) {
        /** Timeouts keep the audio so a tap can resend it. */
        val retryable: Boolean get() = kind == Kind.TIMEOUT
    }

    data class Health(
        val ok: Boolean,
        val version: String,
        val engine: String,
        val gpu: Boolean,
        val llm: String,
        val llmOk: Boolean,
        val ready: Boolean,
    )

    data class Dictation(
        val text: String,
        val raw: String,
        val level: String,
        val llmUsed: Boolean,
        val pressEnter: Boolean,
        val empty: Boolean,
        val audioSeconds: Double,
        val totalMs: Double,
    )

    companion object {
        const val MODE_PTT = "ptt"
        const val MODE_HANDSFREE = "handsfree"
        private const val CONNECT_S = 5L
        private const val READ_PTT_S = 30L
        private const val READ_HANDSFREE_S = 120L
        private val WAV = "audio/wav".toMediaType()
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(CONNECT_S, TimeUnit.SECONDS)
        .readTimeout(READ_PTT_S, TimeUnit.SECONDS)
        .writeTimeout(READ_PTT_S, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build()

    private fun baseUrl(): HttpUrl =
        settings.serverUrl.toHttpUrlOrNull() ?: throw ApiException(Kind.BAD_URL, "Server URL is not valid")

    private fun userAgent() = "LocalFlow-Android/${BuildConfig.VERSION_NAME}"

    /** GET /v1/health (no auth). */
    fun health(): Health {
        val url = baseUrl().newBuilder().addPathSegments("v1/health").build()
        val req = Request.Builder().url(url).header("User-Agent", userAgent()).get().build()
        val body = execute(client, req) { code, body ->
            if (code != 200) throw ApiException(Kind.SERVER, errorMessage(body, code), code)
            body
        }
        val j = parseJson(body)
        return Health(
            ok = j.optBoolean("ok", false),
            version = j.optString("version", "?"),
            engine = j.optString("engine", "?"),
            gpu = j.optBoolean("gpu", false),
            llm = j.optString("llm", ""),
            llmOk = j.optBoolean("llm_ok", false),
            ready = j.optBoolean("ready", false),
        )
    }

    /** POST /v1/dictate. [wav] is a complete WAV file; [mode] is [MODE_PTT] or [MODE_HANDSFREE]. */
    fun dictate(wav: ByteArray, mode: String, app: String?): Dictation {
        val urlBuilder = baseUrl().newBuilder().addPathSegments("v1/dictate").addQueryParameter("mode", mode)
        if (!app.isNullOrBlank()) urlBuilder.addQueryParameter("app", app.take(80))
        val req = Request.Builder()
            .url(urlBuilder.build())
            .header("Authorization", "Bearer ${settings.token}")
            .header("User-Agent", userAgent())
            .post(wav.toRequestBody(WAV))
            .build()
        val readS = if (mode == MODE_HANDSFREE) READ_HANDSFREE_S else READ_PTT_S
        val c = client.newBuilder().readTimeout(readS, TimeUnit.SECONDS).writeTimeout(readS, TimeUnit.SECONDS).build()
        val body = execute(c, req) { code, body ->
            when (code) {
                200 -> body
                401 -> throw ApiException(Kind.UNAUTHORIZED, "Wrong token", code)
                503 -> throw ApiException(Kind.UNAVAILABLE, "PC is warming up / paused", code)
                413 -> throw ApiException(Kind.SERVER, "Recording too long for the PC", code)
                else -> throw ApiException(Kind.SERVER, errorMessage(body, code), code)
            }
        }
        val j = parseJson(body)
        val timings = j.optJSONObject("timings")
        return Dictation(
            text = j.optString("text", ""),
            raw = j.optString("raw", ""),
            level = j.optString("level", ""),
            llmUsed = j.optBoolean("llm_used", false),
            pressEnter = j.optBoolean("press_enter", false),
            empty = j.optBoolean("empty", false),
            audioSeconds = j.optDouble("audio_s", 0.0),
            totalMs = timings?.optDouble("total_ms", 0.0) ?: 0.0,
        )
    }

    private fun <T> execute(c: OkHttpClient, req: Request, handle: (Int, String) -> T): T {
        try {
            c.newCall(req).execute().use { resp ->
                val body = resp.body?.string() ?: ""
                return handle(resp.code, body)
            }
        } catch (e: SocketTimeoutException) {
            // Connect timeouts mean the PC is not there; read timeouts mean it is busy.
            if (e.message?.contains("connect", ignoreCase = true) == true) {
                throw ApiException(Kind.NETWORK, "PC not reachable, is Tailscale on?")
            }
            throw ApiException(Kind.TIMEOUT, "PC busy, tap to retry")
        } catch (e: InterruptedIOException) {
            throw ApiException(Kind.TIMEOUT, "PC busy, tap to retry")
        } catch (e: IOException) {
            throw ApiException(Kind.NETWORK, "PC not reachable, is Tailscale on?")
        }
    }

    private fun parseJson(body: String): JSONObject = try {
        JSONObject(body)
    } catch (e: Exception) {
        throw ApiException(Kind.SERVER, "PC sent an unreadable reply")
    }

    private fun errorMessage(body: String, code: Int): String {
        val fromServer = try { JSONObject(body).optString("error", "") } catch (ignored: Exception) { "" }
        return if (fromServer.isNotBlank()) fromServer else "PC error $code"
    }
}
