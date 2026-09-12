package tech.datafying.localflow

import android.content.Context
import android.content.SharedPreferences

/** All persisted state, in one SharedPreferences file. Keys are stable; do not rename them. */
class Settings(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("localflow", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_URL, DEFAULT_URL)?.takeIf { it.isNotBlank() } ?: DEFAULT_URL
        set(v) = prefs.edit().putString(KEY_URL, normaliseUrl(v)).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(v) = prefs.edit().putString(KEY_TOKEN, v.trim()).apply()

    var showDot: Boolean
        get() = prefs.getBoolean(KEY_SHOW_DOT, false)
        set(v) = prefs.edit().putBoolean(KEY_SHOW_DOT, v).apply()

    var pressEnter: Boolean
        get() = prefs.getBoolean(KEY_PRESS_ENTER, true)
        set(v) = prefs.edit().putBoolean(KEY_PRESS_ENTER, v).apply()

    var haptics: Boolean
        get() = prefs.getBoolean(KEY_HAPTICS, true)
        set(v) = prefs.edit().putBoolean(KEY_HAPTICS, v).apply()

    var sounds: Boolean
        get() = prefs.getBoolean(KEY_SOUNDS, false)
        set(v) = prefs.edit().putBoolean(KEY_SOUNDS, v).apply()

    /** The runtime microphone prompt has been shown at least once (after two denials Android stops showing it). */
    var micAsked: Boolean
        get() = prefs.getBoolean(KEY_MIC_ASKED, false)
        set(v) = prefs.edit().putBoolean(KEY_MIC_ASKED, v).apply()

    /** Dot position in window coordinates; -1 = never placed. */
    var dotX: Int
        get() = prefs.getInt(KEY_DOT_X, -1)
        set(v) = prefs.edit().putInt(KEY_DOT_X, v).apply()

    var dotY: Int
        get() = prefs.getInt(KEY_DOT_Y, -1)
        set(v) = prefs.edit().putInt(KEY_DOT_Y, v).apply()

    companion object {
        const val DEFAULT_URL = ""  // filled in by "Paste setup line"; looks like http://localflow-pc.<tailnet>.ts.net

        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_SHOW_DOT = "show_dot"
        private const val KEY_PRESS_ENTER = "press_enter"
        private const val KEY_HAPTICS = "haptics"
        private const val KEY_SOUNDS = "sounds"
        private const val KEY_MIC_ASKED = "mic_asked"
        private const val KEY_DOT_X = "dot_x"
        private const val KEY_DOT_Y = "dot_y"

        /** Trim, add a scheme if missing, drop a trailing slash. */
        fun normaliseUrl(raw: String): String {
            var u = raw.trim()
            if (u.isEmpty()) return DEFAULT_URL
            if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
            return u.trimEnd('/')
        }

        /**
         * The PC tray copies "URL|TOKEN" to the clipboard under Phone setup.
         * Returns (url, token) or null when the text is not that shape.
         */
        fun parseSetupLine(text: String?): Pair<String, String>? {
            val line = text?.trim()?.lines()?.firstOrNull { it.contains('|') } ?: return null
            val bar = line.indexOf('|')
            val url = line.substring(0, bar).trim()
            val token = line.substring(bar + 1).trim()
            if (url.isEmpty() || token.isEmpty() || token.contains('|')) return null
            if (!url.startsWith("http://") && !url.startsWith("https://") && !url.contains('.')) return null
            return normaliseUrl(url) to token
        }
    }
}
