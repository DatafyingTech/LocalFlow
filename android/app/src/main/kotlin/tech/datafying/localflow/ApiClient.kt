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
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.PortUnreachableException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit

/**
 * The phone side of docs/API.md. Blocking calls; run them off the main thread.
 * Every failure is an [ApiException] whose [ApiException.kind] maps to a dot state and a toast.
 */
class ApiClient(private val settings: Settings) {

    /**
     * NO_DNS / REFUSED / UNREACHABLE split what used to be one blurry NETWORK case. They are
     * three different jobs for the user: turn Tailscale on, start LocalFlow on the PC, wake the
     * PC up. NETWORK stays as the catch-all for an IOException none of them explain.
     */
    enum class Kind { UNAUTHORIZED, UNAVAILABLE, TIMEOUT, NO_DNS, REFUSED, UNREACHABLE, NETWORK, SERVER, BAD_URL }

    class ApiException(
        val kind: Kind,
        message: String,
        val status: Int = 0,
        /** Exception class that actually failed, for the Diagnose button. Never shown in a toast. */
        val causeName: String = "",
    ) : Exception(message) {
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
        private const val WARM_CONNECT_S = 2L
        private const val WARM_READ_S = 3L
        private val WAV = "audio/wav".toMediaType()

        const val MSG_URL_BLANK = "No PC address set. Paste the setup line from your PC."
        const val MSG_URL_MALFORMED = "That address does not look right. Expected http://pc-name.tailnet.ts.net"

        // The three ways "PC not reachable" actually happens, each with its own fix.
        const val MSG_NO_DNS = "Tailscale is off on this phone, or the PC name is wrong"
        const val MSG_REFUSED = "The PC is on the network but LocalFlow is not running on it"
        const val MSG_UNREACHABLE = "The PC is offline or asleep"
        const val MSG_NETWORK = "Could not reach the PC"

        /**
         * Which of the three failures is this?
         *
         * - the name did not resolve            -> Tailscale is off here / wrong PC name
         * - the host answered with a reset       -> the PC is up, LocalFlow is not
         * - nothing answered at all, or no route -> the PC is off or asleep
         *
         * ConnectException covers both "refused" and "timed out"/"no route" depending on the
         * message the OS put in it, so the text is what separates them. UnknownHostException is
         * checked first because it is also an IOException.
         */
        fun kindForIo(e: IOException): Kind {
            val msg = (e.message ?: "") + " " + (e.cause?.message ?: "")
            return when {
                e is UnknownHostException -> Kind.NO_DNS
                e is NoRouteToHostException || e is PortUnreachableException -> Kind.UNREACHABLE
                e is SocketTimeoutException -> Kind.UNREACHABLE
                e is ConnectException && msg.contains("refused", ignoreCase = true) -> Kind.REFUSED
                e is ConnectException && msg.contains("ECONNREFUSED", ignoreCase = true) -> Kind.REFUSED
                e is ConnectException -> Kind.UNREACHABLE
                msg.contains("ECONNREFUSED", ignoreCase = true) -> Kind.REFUSED
                msg.contains("unable to resolve host", ignoreCase = true) -> Kind.NO_DNS
                else -> Kind.NETWORK
            }
        }

        /** The one sentence the user sees for a [Kind]. */
        fun messageFor(kind: Kind): String = when (kind) {
            Kind.NO_DNS -> MSG_NO_DNS
            Kind.REFUSED -> MSG_REFUSED
            Kind.UNREACHABLE -> MSG_UNREACHABLE
            Kind.NETWORK -> MSG_NETWORK
            Kind.TIMEOUT -> "PC busy, tap to retry"
            Kind.UNAUTHORIZED -> "Wrong token"
            Kind.UNAVAILABLE -> "PC is warming up / paused"
            Kind.BAD_URL -> MSG_URL_MALFORMED
            Kind.SERVER -> "The PC answered with an error"
        }

        /** Blank means "never set up"; malformed means the user typed something that is not a URL. */
        fun classifyUrl(raw: String): UrlProblem = when {
            raw.isBlank() -> UrlProblem.BLANK
            raw.trim().toHttpUrlOrNull() == null -> UrlProblem.MALFORMED
            else -> UrlProblem.NONE
        }

        /** Human-readable reason the address cannot be used, or null when it can. */
        fun urlProblemMessage(raw: String): String? = when (classifyUrl(raw)) {
            UrlProblem.BLANK -> MSG_URL_BLANK
            UrlProblem.MALFORMED -> MSG_URL_MALFORMED
            UrlProblem.NONE -> null
        }
    }

    enum class UrlProblem { NONE, BLANK, MALFORMED }

    private val client = OkHttpClient.Builder()
        .connectTimeout(CONNECT_S, TimeUnit.SECONDS)
        .readTimeout(READ_PTT_S, TimeUnit.SECONDS)
        .writeTimeout(READ_PTT_S, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build()

    private fun baseUrl(): HttpUrl {
        val raw = settings.serverUrl
        return raw.trim().toHttpUrlOrNull()
            ?: throw ApiException(Kind.BAD_URL, urlProblemMessage(raw) ?: MSG_URL_MALFORMED)
    }

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
                401 -> throw ApiException(Kind.UNAUTHORIZED, messageFor(Kind.UNAUTHORIZED), code)
                503 -> throw ApiException(Kind.UNAVAILABLE, messageFor(Kind.UNAVAILABLE), code)
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

    /**
     * POST /v1/warm: ask the PC to start reloading its cleanup model now, so the reload hides
     * behind the user speaking instead of delaying the first result after an idle spell.
     * Fire-and-forget: short timeouts, and every failure (including a missing endpoint on an
     * older PC build) is swallowed. Never throws, never changes any state.
     */
    fun warm() {
        try {
            val url = baseUrl().newBuilder().addPathSegments("v1/warm").build()
            val req = Request.Builder()
                .url(url)
                .header("Authorization", "Bearer ${settings.token}")
                .header("User-Agent", userAgent())
                .post(ByteArray(0).toRequestBody(null))
                .build()
            val c = client.newBuilder()
                .connectTimeout(WARM_CONNECT_S, TimeUnit.SECONDS)
                .readTimeout(WARM_READ_S, TimeUnit.SECONDS)
                .writeTimeout(WARM_READ_S, TimeUnit.SECONDS)
                .build()
            c.newCall(req).execute().close()
        } catch (ignored: Exception) {
            // Best effort only; the dictation call reports real problems.
        }
    }

    private fun <T> execute(c: OkHttpClient, req: Request, handle: (Int, String) -> T): T {
        try {
            c.newCall(req).execute().use { resp ->
                val body = resp.body?.string() ?: ""
                return handle(resp.code, body)
            }
        } catch (e: SocketTimeoutException) {
            // Connect timeouts mean the PC is not there; read timeouts mean it is busy thinking.
            if (e.message?.contains("connect", ignoreCase = true) == true) {
                throw ApiException(Kind.UNREACHABLE, MSG_UNREACHABLE, causeName = e.javaClass.simpleName)
            }
            throw ApiException(Kind.TIMEOUT, messageFor(Kind.TIMEOUT), causeName = e.javaClass.simpleName)
        } catch (e: InterruptedIOException) {
            throw ApiException(Kind.TIMEOUT, messageFor(Kind.TIMEOUT), causeName = e.javaClass.simpleName)
        } catch (e: IOException) {
            val kind = kindForIo(e)
            throw ApiException(kind, messageFor(kind), causeName = e.javaClass.simpleName)
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
