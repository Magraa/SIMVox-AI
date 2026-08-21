import os
import sys
import webview
import threading
from pathlib import Path
from config import BASE_DIR, logger
from desktop_bridge import DesktopBridge

def create_app():
    # Path to local UI index.html file
    ui_index_path = BASE_DIR / "ui" / "index.html"
    
    bridge = DesktopBridge()
    
    # Create pywebview window
    window = webview.create_window(
        title="Automated AI Calling System - Control Panel",
        url=f"file:///{ui_index_path.as_posix()}",
        js_api=bridge,
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#0B0F17",
        resizable=True
    )
    
    bridge.set_window(window)
    
    # System tray support via pystray (optional / non-blocking)
    def setup_tray():
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Create a simple icon image dynamically if icon file doesn't exist
            icon_img = Image.new('RGB', (64, 64), color=(0, 242, 254))
            draw = ImageDraw.Draw(icon_img)
            draw.rectangle([16, 16, 48, 48], fill=(11, 15, 23))

            def on_show(icon, item):
                window.show()
                window.restore()

            def on_exit(icon, item):
                icon.stop()
                window.destroy()

            menu = pystray.Menu(
                pystray.MenuItem("Open Dashboard", on_show, default=True),
                pystray.MenuItem("Exit AI Calling", on_exit)
            )

            tray_icon = pystray.Icon("ai_calling", icon_img, "AI Calling System", menu)
            tray_icon.run()
        except Exception as e:
            logger.warning("System tray initialization skipped: %s", e)

    tray_thread = threading.Thread(target=setup_tray, daemon=True)
    tray_thread.start()

    logger.info("Starting pywebview Native Desktop Window...")
    webview.start(debug=False)

if __name__ == "__main__":
    create_app()
