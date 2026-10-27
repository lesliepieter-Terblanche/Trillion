package com.trillion.tvlauncher

import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.ResolveInfo
import android.graphics.Color
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.GridLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Trillion TV Launcher — a light, native home screen for an Android TV.
 *
 * Design goals (learned the hard way): list EVERY launchable app, including the TV's hidden
 * input / "Sources" app so the HDMI/Chromecast is reachable, and never draw an overlay over the
 * HDMI surface. It never touches the stock launcher, so it's always recoverable.
 */
class MainActivity : AppCompatActivity() {

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var clock: TextView
    private lateinit var appsGrid: GridLayout
    private lateinit var favGrid: GridLayout

    // apps to surface big at the top when installed, matched by label (case-insensitive contains)
    private val FAVES = listOf(
        "netflix", "youtube", "smarttube", "dstv", "stremio", "iptv", "tivimate", "smarters",
        "kodi", "plex", "showmax", "prime video", "disney", "spotify", "vlc"
    )

    private val clockTick = object : Runnable {
        override fun run() {
            clock.text = SimpleDateFormat("EEE d MMM  ·  HH:mm", Locale.getDefault()).format(Date())
            handler.postDelayed(this, 10_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.decorView.setBackgroundColor(Color.parseColor("#0a0a0f"))
        setContentView(buildUi())
    }

    override fun onResume() {
        super.onResume()
        handler.post(clockTick)
        loadApps()               // refresh in case apps were installed/removed
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(clockTick)
    }

    // ---- UI ----
    private fun dp(v: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun buildUi(): View {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setPadding(dp(40), dp(32), dp(40), dp(40))
        }
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        clock = TextView(this).apply {
            setTextColor(Color.parseColor("#f3f3f8"))
            textSize = 22f
            setPadding(0, 0, 0, dp(20))
        }
        root.addView(clock)

        root.addView(sectionLabel("Favourites"))
        favGrid = GridLayout(this).apply {
            columnCount = 4
            alignmentMode = GridLayout.ALIGN_BOUNDS
        }
        root.addView(favGrid)

        root.addView(sectionLabel("All apps"))
        appsGrid = GridLayout(this).apply {
            columnCount = 5
            alignmentMode = GridLayout.ALIGN_BOUNDS
        }
        root.addView(appsGrid)

        root.addView(sectionLabel("Inputs & settings"))
        root.addView(buildSettingsRow())

        scroll.addView(root)
        return scroll
    }

    private fun sectionLabel(text: String): TextView = TextView(this).apply {
        this.text = text.uppercase(Locale.getDefault())
        setTextColor(Color.parseColor("#9a9ab0"))
        textSize = 12f
        letterSpacing = 0.12f
        setPadding(dp(4), dp(22), 0, dp(12))
    }

    private fun tileBackground(focused: Boolean): Drawable = GradientDrawable().apply {
        cornerRadius = dp(14).toFloat()
        setColor(if (focused) Color.parseColor("#7c5cff") else Color.parseColor("#15151f"))
        setStroke(dp(1), if (focused) Color.parseColor("#9a7bff") else Color.parseColor("#26263a"))
    }

    private fun makeTile(label: String, icon: Drawable?, onClick: () -> Unit): TextView {
        val tile = TextView(this).apply {
            text = label
            maxLines = 2
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#f3f3f8"))
            textSize = 13f
            isFocusable = true
            isFocusableInTouchMode = false
            setPadding(dp(12), dp(16), dp(12), dp(16))
            background = tileBackground(false)
            if (icon != null) {
                val size = dp(44)
                icon.setBounds(0, 0, size, size)
                setCompoundDrawables(null, icon, null, null)
                compoundDrawablePadding = dp(8)
            }
            setOnClickListener { onClick() }
            setOnFocusChangeListener { v, hasFocus ->
                v.background = tileBackground(hasFocus)
                v.animate().scaleX(if (hasFocus) 1.06f else 1f).scaleY(if (hasFocus) 1.06f else 1f)
                    .setDuration(90).start()
            }
        }
        return tile
    }

    private fun gridParams(big: Boolean = false): GridLayout.LayoutParams = GridLayout.LayoutParams().apply {
        width = if (big) dp(190) else dp(150)
        height = if (big) dp(140) else dp(120)
        setMargins(dp(6), dp(6), dp(6), dp(6))
    }

    // ---- apps ----
    @SuppressLint("QueryPermissionsNeeded")
    private fun loadApps() {
        appsGrid.removeAllViews()
        favGrid.removeAllViews()
        val pm = packageManager
        val seen = HashSet<String>()
        val entries = ArrayList<ResolveInfo>()

        // Leanback (TV) launchers first, then regular launchers — covers the hidden input/Sources app too
        for (cat in listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER)) {
            val intent = Intent(Intent.ACTION_MAIN).addCategory(cat)
            for (ri in pm.queryIntentActivities(intent, 0)) {
                val pkg = ri.activityInfo?.packageName ?: continue
                if (pkg == packageName) continue          // don't list ourselves
                if (seen.add(pkg + "/" + ri.activityInfo.name)) entries.add(ri)
            }
        }
        entries.sortBy { it.loadLabel(pm)?.toString()?.lowercase(Locale.getDefault()) ?: "" }

        var favCount = 0
        for (ri in entries) {
            val label = ri.loadLabel(pm)?.toString() ?: ri.activityInfo.packageName
            val icon = try { ri.loadIcon(pm) } catch (e: Exception) { null }
            val ai = ri.activityInfo
            val open = {
                try {
                    val launch = Intent(Intent.ACTION_MAIN)
                        .addCategory(Intent.CATEGORY_LAUNCHER)
                        .setClassName(ai.packageName, ai.name)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
                    startActivity(launch)
                } catch (e: Exception) {
                    try {
                        startActivity(pm.getLaunchIntentForPackage(ai.packageName)!!)
                    } catch (e2: Exception) {
                        toast("Couldn't open $label")
                    }
                }
            }
            appsGrid.addView(makeTile(label, icon, open), gridParams())

            val lab = label.lowercase(Locale.getDefault())
            if (FAVES.any { lab.contains(it) }) {
                favGrid.addView(makeTile(label, icon, open), gridParams(true))
                favCount++
            }
        }
        if (favCount == 0) {
            favGrid.addView(TextView(this).apply {
                text = "Install Netflix, YouTube, DStv, Stremio, IPTV… and they appear here."
                setTextColor(Color.parseColor("#9a9ab0"))
                textSize = 13f
            })
        }
        if (entries.isEmpty()) {
            appsGrid.addView(TextView(this).apply {
                text = "No apps found."
                setTextColor(Color.parseColor("#9a9ab0"))
            })
        }
    }

    // ---- settings / inputs shortcuts ----
    private fun buildSettingsRow(): View {
        val grid = GridLayout(this).apply { columnCount = 4 }
        val items = listOf(
            "All settings" to Settings.ACTION_SETTINGS,
            "Wi-Fi" to Settings.ACTION_WIFI_SETTINGS,
            "Network" to Settings.ACTION_WIRELESS_SETTINGS,
            "Display" to Settings.ACTION_DISPLAY_SETTINGS,
            "Sound" to Settings.ACTION_SOUND_SETTINGS,
            "Apps" to Settings.ACTION_APPLICATION_SETTINGS,
            "Storage" to Settings.ACTION_INTERNAL_STORAGE_SETTINGS,
            "Date & time" to Settings.ACTION_DATE_SETTINGS,
            "Developer" to Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS,
            "About TV" to Settings.ACTION_DEVICE_INFO_SETTINGS
        )
        for ((label, action) in items) {
            val tile = makeTile(label, null) {
                try {
                    startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) {
                    try {
                        startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    } catch (e2: Exception) {
                        toast("Couldn't open $label")
                    }
                }
            }
            grid.addView(tile, gridParams())
        }
        return grid
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    // Home button while already on the launcher: do nothing (stay put), never crash
    override fun onBackPressed() { /* stay on the home screen */ }
}
