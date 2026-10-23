---
name: Phone control by screen
description: Operate the user's Android phone through the Mobile MCP connector by reading its on-screen buttons and text, for multi-step jobs in apps where the quick phone tools are not enough.
---

# Phone control by screen

Use this for phone jobs that need several taps inside an app ("open WhatsApp, find Brandon, send him the photo",
"turn on hotspot", "check my last Takealot order"). For simple jobs Trillion already has quick phone tools; use those
first and come here when they cannot do it. The connector is **Phone control (Mobile MCP)**.

## How to do it

1. Call `connector_tools` for "Phone control (Mobile MCP)" once, then `mobile_list_available_devices`.
   Use **only this login's own phone** (the serial set on the Phone page). Never act on someone else's phone.
2. Open the app with `mobile_launch_app` (package name) or `mobile_open_url`.
3. **Read before you tap:** call `mobile_list_elements_on_screen`. It returns each button, field and text with its
   position. Tap by those positions with `mobile_click_on_screen_at_coordinates`. Only take a screenshot when the
   list is empty (games, video, some banking apps).
4. Type with `mobile_type_keys`, go back with `mobile_press_button` "BACK", scroll with `mobile_swipe_on_screen`.
5. After each step, list the elements again to confirm the screen changed as expected before the next tap.
6. Finish by saying in one line what was done, and what is now on screen.

## Rules

- Never use the remote cloud devices (`mobile_list_remote_devices`, `mobile_allocate_remote_device`,
  `mobile_login_to_cloud_provider`). Only the user's own phone on USB or Wi-Fi debugging.
- Never enter passwords, PINs, card numbers or banking one-time codes. Stop and ask the user to do that part.
- Do not install or uninstall apps unless the user asked for exactly that.
- If no phone is listed, say the phone is not connected (USB debugging off, cable, or not allowed on the phone) and stop.
