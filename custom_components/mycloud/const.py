"""Constants for the My Cloud integration."""

from homeassistant.const import Platform

DOMAIN = "mycloud"

HOST = "Host"
USERNAME = "Username"
PASSWORD = "Password"
VERSION = "Version"
RELEASE_ADMIN_SESSION = "release_admin_session_after_polling"

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON)

DEFAULT_UPDATE_INTERVAL = 600
DEFAULT_RELEASE_ADMIN_SESSION = False
MIN_UPDATE_INTERVAL = 30
CONFIG_FLOW_TIMEOUT = 15
UPDATE_TIMEOUT = 60
