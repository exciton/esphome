import esphome.codegen as cg
from tests.testing_helpers import ComponentManifestOverride


def override_manifest(manifest: ComponentManifestOverride) -> None:
    # Enables light_json_schema.cpp without USE_MQTT, which pulls mqtt code into core/util.cpp
    async def to_code_testing(config):
        cg.add_define("USE_WEBSERVER")
        # Code guarded by USE_WEBSERVER (e.g. api_connection.cpp) also needs the port
        cg.add_define("USE_WEBSERVER_PORT", 80)

    manifest.to_code = to_code_testing
    manifest.dependencies = manifest.dependencies + ["json"]
