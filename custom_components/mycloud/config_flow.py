import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from .const import DOMAIN

class MyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="WD My Cloud Integration", data=user_input)

        schema = vol.Schema({
            vol.Required("Host"): str,
            vol.Required("Username"): str,
            vol.Required("Password"): str,
            vol.Required("Version"): vol.In([2, 5])
        })
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors={}
        )