# <img src="images/logo_WDMyCloud_2_1.png" alt="WD My Cloud App Icon" width="100"> ha-mycloud

A Home Assistant integration for Western Digital My Cloud NAS devices.

---

### Features
- **System Status**: Monitor CPU and memory usage of your My Cloud device.
- **Device Information**: See key details like serial number, name, and firmware version.
- **Network & Storage**: Track network info, alerts, and shared folder names.
- **Account Management**: View user accounts and groups.

---

### Installation

#### HACS (Recommended)
1. Add this repository as a custom repository in HACS.
2. Search for "WD My Cloud" and install the integration.
3. Restart Home Assistant.

#### Manual
1. Copy the `custom_components/ha-mycloud` folder into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

---

### Configuration
1. In the Home Assistant UI, go to **Settings** > **Devices & Services** > **Integrations**.
2. Click **Add Integration** and search for "WD My Cloud".
3. Follow the on-screen instructions to enter your device's host, username, and password.

---

### Sensors
The integration provides the following sensors:
- **WD My Cloud CPU Usage** (`sensor.wd_my_cloud_cpu_usage`)
- **WD My Cloud Memory Usage** (`sensor.wd_my_cloud_memory_usage`)