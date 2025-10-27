import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN

class MyCloudOptionsFlowHandler(config_entries.OptionsFlow):

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get("update_interval", 600)
        
        options_schema = vol.Schema({
            vol.Optional(
                "update_interval", 
                default=current_interval,
            ): vol.All(vol.Coerce(int), vol.Range(min=30))
        })

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            description_placeholders={"example": "Minimum 30 seconds."}
        )

class MyCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MyCloudOptionsFlowHandler(config_entry)

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