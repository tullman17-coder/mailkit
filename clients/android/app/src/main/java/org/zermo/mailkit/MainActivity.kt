package org.zermo.mailkit

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private val prefs by lazy { getSharedPreferences("mailkit", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyIntent(intent)
        if (prefs.getBoolean("connected", false) && !prefs.getString("token", "").isNullOrEmpty()) {
            showEngine()
        } else {
            showConnect()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        applyIntent(intent)
        if (prefs.getBoolean("connected", false)) showEngine()
    }

    private fun applyIntent(intent: Intent?) {
        val data: Uri = intent?.data ?: return
        val pair = Pairing.parse(data) ?: return
        prefs.edit()
            .putString("url", pair.first)
            .putString("token", pair.second)
            .putBoolean("connected", true)
            .apply()
    }

    private fun showConnect() {
        setContentView(R.layout.activity_connect)
        val urlField = findViewById<EditText>(R.id.engine_url)
        val tokenField = findViewById<EditText>(R.id.engine_token)
        urlField.setText(prefs.getString("url", "http://192.168.1.10:8765"))
        tokenField.setText(prefs.getString("token", ""))
        findViewById<Button>(R.id.connect).setOnClickListener {
            val url = urlField.text.toString().trim()
            val token = tokenField.text.toString().trim()
            if (url.isEmpty() || token.isEmpty()) {
                Toast.makeText(this, "Engine URL and token are required.", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit().putString("url", url).putString("token", token).putBoolean("connected", true).apply()
            showEngine()
        }
    }

    private fun showEngine() {
        val web = WebView(this)
        web.webViewClient = WebViewClient()
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.cacheMode = WebSettings.LOAD_NO_CACHE
        setContentView(web)
        val url = prefs.getString("url", "") ?: return
        val token = prefs.getString("token", "") ?: return
        web.loadUrl(Pairing.webUrl(url, token))
    }
}
