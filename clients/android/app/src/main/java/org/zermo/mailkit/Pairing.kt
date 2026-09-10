package org.zermo.mailkit

import android.net.Uri

object Pairing {
    fun parse(uri: Uri): Pair<String, String>? {
        if (uri.scheme != "mailkit") return null
        val url = uri.getQueryParameter("url") ?: return null
        val token = uri.getQueryParameter("token") ?: return null
        if (url.isEmpty() || token.isEmpty()) return null
        return url to token
    }

    fun webUrl(engine: String, token: String): String {
        val parsed = Uri.parse(engine).buildUpon()
            .appendQueryParameter("token", token)
            .appendQueryParameter("shell", "mobile")
            .build()
        return parsed.toString()
    }
}
