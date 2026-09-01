from logging import getLogger
import math
import re

from esphome import automation, pins
import esphome.codegen as cg
from esphome.components.const import CONF_DATA_BITS, CONF_PARITY, CONF_STOP_BITS
from esphome.components.esp32 import get_esp32_variant
from esphome.components.esp32.const import (
    VARIANT_ESP32,
    VARIANT_ESP32C2,
    VARIANT_ESP32C3,
    VARIANT_ESP32C5,
    VARIANT_ESP32C6,
    VARIANT_ESP32C61,
    VARIANT_ESP32H2,
    VARIANT_ESP32H4,
    VARIANT_ESP32H21,
    VARIANT_ESP32P4,
    VARIANT_ESP32S2,
    VARIANT_ESP32S3,
    VARIANT_ESP32S31,
)
from esphome.config_helpers import (
    filter_source_files_from_defines,
    filter_source_files_from_platform,
)
import esphome.config_validation as cv
from esphome.const import (
    CONF_AFTER,
    CONF_BAUD_RATE,
    CONF_BYTES,
    CONF_DATA,
    CONF_DEBUG,
    CONF_DELIMITER,
    CONF_DIRECTION,
    CONF_DUMMY_RECEIVER,
    CONF_DUMMY_RECEIVER_ID,
    CONF_FLOW_CONTROL_PIN,
    CONF_HARDWARE_UART,
    CONF_ID,
    CONF_LAMBDA,
    CONF_LOGGER,
    CONF_NUMBER,
    CONF_PORT,
    CONF_RX_BUFFER_SIZE,
    CONF_RX_PIN,
    CONF_SEQUENCE,
    CONF_TIMEOUT,
    CONF_TRIGGER_ID,
    CONF_TX_PIN,
    CONF_UART_ID,
    PLATFORM_HOST,
    PlatformFramework,
)
from esphome.core import CORE, ID, CoroPriority, coroutine_with_priority
import esphome.final_validate as fv
from esphome.types import ConfigType
from esphome.yaml_util import make_data_base

_LOGGER = getLogger(__name__)

CODEOWNERS = ["@esphome/core"]
DOMAIN = "uart"


uart_ns = cg.esphome_ns.namespace("uart")
UARTComponent = uart_ns.class_("UARTComponent")

IDFUARTComponent = uart_ns.class_("IDFUARTComponent", UARTComponent, cg.Component)
ESP8266UartComponent = uart_ns.class_(
    "ESP8266UartComponent", UARTComponent, cg.Component
)
RP2UartComponent = uart_ns.class_("RP2UartComponent", UARTComponent, cg.Component)
LibreTinyUARTComponent = uart_ns.class_(
    "LibreTinyUARTComponent", UARTComponent, cg.Component
)
HostUartComponent = uart_ns.class_("HostUartComponent", UARTComponent, cg.Component)


NATIVE_UART_CLASSES = (
    str(IDFUARTComponent),
    str(ESP8266UartComponent),
    str(RP2UartComponent),
    str(LibreTinyUARTComponent),
)

HOST_BAUD_RATES = [
    50,
    75,
    110,
    134,
    150,
    200,
    300,
    600,
    1200,
    1800,
    2400,
    4800,
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
    500000,
    576000,
    921600,
    1000000,
    1152000,
    1500000,
    2000000,
    2500000,
    3000000,
    3500000,
    4000000,
]

UARTDevice = uart_ns.class_("UARTDevice")
UARTWriteAction = uart_ns.class_("UARTWriteAction", automation.Action)
UARTDebugger = uart_ns.class_("UARTDebugger", cg.Component, automation.Action)
UARTDummyReceiver = uart_ns.class_("UARTDummyReceiver", cg.Component)
MULTI_CONF = True
MULTI_CONF_NO_DEFAULT = True


def validate_raw_data(value):
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return cv.Schema([cv.hex_uint8_t])(value)
    raise cv.Invalid(
        "data must either be a string wrapped in quotes or a list of bytes"
    )


def validate_rx_pin(value):
    value = pins.internal_gpio_input_pin_schema(value)
    if CORE.is_esp8266 and value[CONF_NUMBER] >= 16:
        raise cv.Invalid("Pins GPIO16 and GPIO17 cannot be used as RX pins on ESP8266.")
    return value


def validate_host_config(config):
    if CORE.is_host:
        if CONF_TX_PIN in config or CONF_RX_PIN in config:
            raise cv.Invalid(
                "TX and RX pins are not supported for UART on host platform."
            )
        if config[CONF_BAUD_RATE] not in HOST_BAUD_RATES:
            raise cv.Invalid(
                f"Host platform doesn't support baud rate {config[CONF_BAUD_RATE]}",
                path=[CONF_BAUD_RATE],
            )
    return config


def validate_rx_buffer_size(config):
    if CORE.is_esp32:
        # ESP32 UART hardware FIFO is 128 bytes (LP UART is 16 bytes, but we use 128 as safe minimum)
        # rx_buffer_size must be greater than the hardware FIFO length
        min_buffer_size = 128
        if config[CONF_RX_BUFFER_SIZE] <= min_buffer_size:
            _LOGGER.warning(
                "UART rx_buffer_size (%d bytes) is too small and must be greater than the hardware "
                "FIFO size (%d bytes). The buffer size will be automatically adjusted at runtime.",
                config[CONF_RX_BUFFER_SIZE],
                min_buffer_size,
            )
    return config


def _uart_declare_type(value):
    if CORE.is_esp8266:
        return cv.declare_id(ESP8266UartComponent)(value)
    if CORE.is_esp32:
        return cv.declare_id(IDFUARTComponent)(value)
    if CORE.is_rp2:
        return cv.declare_id(RP2UartComponent)(value)
    if CORE.is_libretiny:
        return cv.declare_id(LibreTinyUARTComponent)(value)
    if CORE.is_host:
        return cv.declare_id(HostUartComponent)(value)
    raise NotImplementedError


UARTParityOptions = uart_ns.enum("UARTParityOptions")
UART_PARITY_OPTIONS = {
    "NONE": UARTParityOptions.UART_CONFIG_PARITY_NONE,
    "EVEN": UARTParityOptions.UART_CONFIG_PARITY_EVEN,
    "ODD": UARTParityOptions.UART_CONFIG_PARITY_ODD,
}

CONF_FLUSH_TIMEOUT = "flush_timeout"
CONF_RX_FULL_THRESHOLD = "rx_full_threshold"
CONF_RX_TIMEOUT = "rx_timeout"

UARTDirection = uart_ns.enum("UARTDirection")
UART_DIRECTIONS = {
    "RX": UARTDirection.UART_DIRECTION_RX,
    "TX": UARTDirection.UART_DIRECTION_TX,
    "BOTH": UARTDirection.UART_DIRECTION_BOTH,
}

# The reason for having CONF_BYTES at 150 by default:
#
# The log message buffer size is 512 bytes by default. About 35 bytes are
# used for the log prefix. That leaves us with 477 bytes for logging data.
# The default log output is hex, which uses 3 characters per represented
# byte (2 hex chars + 1 separator). That means that 477 / 3 = 159 bytes
# can be represented in a single log line. Using 150, because people love
# round numbers.
AFTER_DEFAULTS = {CONF_BYTES: 150, CONF_TIMEOUT: "100ms"}

CONF_DEBUG_PREFIX = "debug_prefix"

# By default, log in hex format when no specific sequence is provided.
DEFAULT_DEBUG_OUTPUT = "UARTDebug::log_hex(direction, bytes, ':', debug_prefix);"
DEFAULT_SEQUENCE = [{CONF_LAMBDA: make_data_base(DEFAULT_DEBUG_OUTPUT)}]


def maybe_empty_debug(value):
    if value is None:
        value = {}
    return DEBUG_SCHEMA(value)


def validate_port(value):
    if not re.match(r"^/(?:[^/]+/)[^/]+$", value):
        raise cv.Invalid("Port must be a valid device path")
    return value


DEBUG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(UARTDebugger),
        cv.Optional(CONF_DIRECTION, default="BOTH"): cv.enum(
            UART_DIRECTIONS, upper=True
        ),
        cv.Optional(CONF_AFTER, default=AFTER_DEFAULTS): cv.Schema(
            {
                cv.Optional(
                    CONF_BYTES, default=AFTER_DEFAULTS[CONF_BYTES]
                ): cv.validate_bytes,
                cv.Optional(
                    CONF_TIMEOUT, default=AFTER_DEFAULTS[CONF_TIMEOUT]
                ): cv.positive_time_period_milliseconds,
                cv.Optional(CONF_DELIMITER): cv.templatable(validate_raw_data),
            }
        ),
        cv.Optional(
            CONF_SEQUENCE, default=DEFAULT_SEQUENCE
        ): automation.validate_automation(),
        cv.Optional(CONF_DUMMY_RECEIVER, default=False): cv.boolean,
        cv.GenerateID(CONF_DUMMY_RECEIVER_ID): cv.declare_id(UARTDummyReceiver),
        cv.Optional(CONF_DEBUG_PREFIX, default=""): cv.string,
    }
)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): _uart_declare_type,
            cv.Required(CONF_BAUD_RATE): cv.int_range(min=1),
            cv.Optional(CONF_TX_PIN): pins.internal_gpio_output_pin_schema,
            cv.Optional(CONF_RX_PIN): validate_rx_pin,
            cv.Optional(CONF_FLOW_CONTROL_PIN): cv.All(
                cv.only_on_esp32, pins.internal_gpio_output_pin_schema
            ),
            cv.Optional(CONF_PORT): cv.All(validate_port, cv.only_on(PLATFORM_HOST)),
            cv.Optional(CONF_RX_BUFFER_SIZE, default=256): cv.validate_bytes,
            cv.Optional(CONF_RX_FULL_THRESHOLD): cv.All(
                cv.only_on_esp32, cv.validate_bytes, cv.int_range(min=1, max=120)
            ),
            cv.SplitDefault(CONF_RX_TIMEOUT, esp32=2): cv.All(
                cv.only_on_esp32, cv.validate_bytes, cv.int_range(min=0, max=92)
            ),
            cv.Optional(CONF_FLUSH_TIMEOUT): cv.All(
                cv.only_on_esp32, cv.positive_time_period_milliseconds
            ),
            cv.Optional(CONF_STOP_BITS, default=1): cv.one_of(1, 2, int=True),
            cv.Optional(CONF_DATA_BITS, default=8): cv.int_range(min=5, max=8),
            cv.Optional(CONF_PARITY, default="NONE"): cv.enum(
                UART_PARITY_OPTIONS, upper=True
            ),
            cv.Optional(CONF_DEBUG): maybe_empty_debug,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.has_at_least_one_key(CONF_TX_PIN, CONF_RX_PIN, CONF_PORT),
    validate_host_config,
    validate_rx_buffer_size,
)

# Number of hardware UART ports on each ESP32 variant. Values are SOC_UART_NUM from
# ESP-IDF, in components/soc/<target>/include/soc/soc_caps.h. Where a variant has a low
# power UART that port is included, matching how the C++ side counts them.
ESP32_UART_PORTS = {
    VARIANT_ESP32: 3,
    VARIANT_ESP32C2: 2,
    VARIANT_ESP32C3: 2,
    VARIANT_ESP32C5: 3,
    VARIANT_ESP32C6: 3,
    VARIANT_ESP32C61: 3,
    VARIANT_ESP32H2: 2,
    VARIANT_ESP32H4: 2,
    VARIANT_ESP32H21: 2,
    VARIANT_ESP32P4: 6,
    VARIANT_ESP32S2: 2,
    VARIANT_ESP32S3: 3,
    VARIANT_ESP32S31: 3,
}

# The logger's hardware_uart values that put its output on a hardware UART port, and the
# port each one means. Any other value sends the log over USB instead.
LOGGER_UART_PORTS = {"UART0": 0, "UART1": 1, "UART2": 2}

# ESP-IDF puts its serial console, which carries the bootloader banner and the crash
# output, on UART0. Only the logger's USB settings move it off the hardware UARTs.
CONSOLE_UART_PORT = 0


def _esp32_reserved_ports(full_config: ConfigType) -> tuple[int | None, int | None]:
    """Return the ports the ESP-IDF console and the logger occupy, if any."""
    logger_config = full_config.get(CONF_LOGGER)
    if logger_config is None:
        # With no logger the console keeps ESP-IDF's default of UART0
        return CONSOLE_UART_PORT, None
    hardware_uart = logger_config.get(CONF_HARDWARE_UART)
    console_port = CONSOLE_UART_PORT if hardware_uart in LOGGER_UART_PORTS else None
    logger_port = None
    if logger_config.get(CONF_BAUD_RATE):
        logger_port = LOGGER_UART_PORTS.get(hardware_uart)
    return console_port, logger_port


def _esp32_assign_ports(
    bus_count: int, port_count: int, console_port: int | None, logger_port: int | None
) -> list[int | None]:
    """Work out the port each bus will be given, as IDFUARTComponent::setup() does.

    Buses are placed on a port nothing else drives first, because assigning pins to a port
    moves whatever that port already carries onto those pins. The console port is only used
    once every other port is gone; the logger's port is never used.
    """
    taken = set() if logger_port is None else {logger_port}
    assigned: list[int | None] = []
    for _ in range(bus_count):
        port = next(
            (p for p in range(port_count) if p not in taken and p != console_port), None
        )
        if port is None:
            port = next((p for p in range(port_count) if p not in taken), None)
        if port is not None:
            taken.add(port)
        assigned.append(port)
    return assigned


def _check_esp32_ports(config: ConfigType) -> ConfigType:
    """Report buses that cannot get a hardware UART port of their own."""
    if not CORE.is_esp32:
        return config
    port_count = ESP32_UART_PORTS.get(get_esp32_variant())
    if port_count is None:
        return config

    full_config = fv.full_config.get()
    bus_configs = full_config.get(DOMAIN, [])
    index = next(
        (i for i, c in enumerate(bus_configs) if c[CONF_ID] == config[CONF_ID]), None
    )
    if index is None:
        return config

    console_port, logger_port = _esp32_reserved_ports(full_config)
    port = _esp32_assign_ports(len(bus_configs), port_count, console_port, logger_port)[
        index
    ]

    if port is None:
        used_by_logger = (
            " One of them is used by the logger; set baud_rate to 0 there, or move the "
            "logger to USB_CDC or USB_SERIAL_JTAG, to free it."
            if logger_port is not None
            else ""
        )
        raise cv.Invalid(
            f"This board has only {port_count} hardware UART ports and they are all in "
            f"use, so this bus cannot be set up.{used_by_logger}"
        )

    if port == console_port:
        _LOGGER.warning(
            "UART bus '%s' will be given UART%d, which ESP-IDF also uses for its serial "
            "console. Setting pins on this bus moves the console onto them, so the "
            "bootloader banner and crash messages will be sent to whatever is wired "
            "there. Set the logger's hardware_uart to USB_CDC or USB_SERIAL_JTAG to move "
            "the console off the hardware UARTs.",
            config[CONF_ID],
            port,
        )

    return config


FINAL_VALIDATE_SCHEMA = _check_esp32_ports


async def debug_to_code(config, parent):
    trigger = cg.new_Pvariable(config[CONF_TRIGGER_ID], parent)
    await cg.register_component(trigger, config)
    for action in config[CONF_SEQUENCE]:
        await automation.build_automation(
            trigger,
            [
                (UARTDirection, "direction"),
                (cg.std_vector.template(cg.uint8), "bytes"),
                (cg.StringRef, "debug_prefix"),
            ],
            action,
        )
    cg.add(trigger.set_direction(config[CONF_DIRECTION]))
    after = config[CONF_AFTER]
    cg.add(trigger.set_after_bytes(after[CONF_BYTES]))
    cg.add(trigger.set_after_timeout(after[CONF_TIMEOUT]))
    if CONF_DELIMITER in after:
        data = after[CONF_DELIMITER]
        if isinstance(data, bytes):
            data = list(data)
        for byte in after[CONF_DELIMITER]:
            cg.add(trigger.add_delimiter_byte(byte))
    if config[CONF_DUMMY_RECEIVER]:
        dummy = cg.new_Pvariable(config[CONF_DUMMY_RECEIVER_ID], parent)
        await cg.register_component(dummy, {})
    if debug_prefix := config[CONF_DEBUG_PREFIX]:
        cg.add(trigger.set_debug_prefix(debug_prefix))
    cg.add_define("USE_UART_DEBUGGER")


async def to_code(config):
    cg.add_global(uart_ns.using)
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_baud_rate(config[CONF_BAUD_RATE]))

    if CONF_TX_PIN in config:
        tx_pin = await cg.gpio_pin_expression(config[CONF_TX_PIN])
        cg.add(var.set_tx_pin(tx_pin))
    if CONF_RX_PIN in config:
        rx_pin = await cg.gpio_pin_expression(config[CONF_RX_PIN])
        cg.add(var.set_rx_pin(rx_pin))
    if CONF_FLOW_CONTROL_PIN in config:
        flow_control_pin = await cg.gpio_pin_expression(config[CONF_FLOW_CONTROL_PIN])
        cg.add(var.set_flow_control_pin(flow_control_pin))
    if CONF_PORT in config:
        cg.add(var.set_name(config[CONF_PORT]))
    cg.add(var.set_rx_buffer_size(config[CONF_RX_BUFFER_SIZE]))
    if CORE.is_esp32:
        if CONF_RX_FULL_THRESHOLD not in config:
            # Calculate rx_full_threshold to be 10ms
            bytelength = config[CONF_DATA_BITS] + config[CONF_STOP_BITS] + 1
            if config[CONF_PARITY] != "NONE":
                bytelength += 1
            config[CONF_RX_FULL_THRESHOLD] = max(
                1,
                min(
                    120,
                    math.floor((config[CONF_BAUD_RATE] / (bytelength * 1000 / 10)) - 1),
                ),
            )
        cg.add(var.set_rx_full_threshold(config[CONF_RX_FULL_THRESHOLD]))
        cg.add(var.set_rx_timeout(config[CONF_RX_TIMEOUT]))
        if CONF_FLUSH_TIMEOUT in config:
            cg.add(var.set_flush_timeout(config[CONF_FLUSH_TIMEOUT]))
    cg.add(var.set_stop_bits(config[CONF_STOP_BITS]))
    cg.add(var.set_data_bits(config[CONF_DATA_BITS]))
    cg.add(var.set_parity(config[CONF_PARITY]))

    if CONF_DEBUG in config:
        await debug_to_code(config[CONF_DEBUG], var)

    # ESP8266: Enable the Arduino Serial objects that might be used based on pin config
    # The C++ code selects hardware serial at runtime based on these pin combinations:
    # - Serial (UART0): TX=1 or null, RX=3 or null
    # - Serial (UART0 swap): TX=15 or null, RX=13 or null
    # - Serial1: TX=2 or null, RX=8 or null
    if CORE.is_esp8266:
        from esphome.components.esp8266.const import enable_serial, enable_serial1

        tx_num = config[CONF_TX_PIN][CONF_NUMBER] if CONF_TX_PIN in config else None
        rx_num = config[CONF_RX_PIN][CONF_NUMBER] if CONF_RX_PIN in config else None

        # Check if this config could use Serial (UART0 regular or swap)
        if (tx_num is None or tx_num in (1, 15)) and (
            rx_num is None or rx_num in (3, 13)
        ):
            enable_serial()
            cg.add_define("USE_ESP8266_UART_SERIAL")
        # Check if this config could use Serial1
        if (tx_num is None or tx_num == 2) and (rx_num is None or rx_num == 8):
            enable_serial1()
            cg.add_define("USE_ESP8266_UART_SERIAL1")

    CORE.add_job(final_step)


# A schema to use for all UART devices, all UART integrations must extend this!
UART_DEVICE_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_UART_ID): cv.use_id(UARTComponent),
    }
)

KEY_UART_DEVICES = "uart_devices"


def final_validate_device_schema(
    name: str,
    *,
    uart_bus: str = CONF_UART_ID,
    baud_rate: int | None = None,
    require_tx: bool = False,
    require_rx: bool = False,
    data_bits: int | None = None,
    parity: str | None = None,
    stop_bits: int | None = None,
):
    def validate_baud_rate(value):
        if value != baud_rate:
            raise cv.Invalid(
                f"Component {name} requires baud rate {baud_rate} for the uart referenced by {uart_bus}"
            )
        return value

    def validate_pin(opt, device):
        def validator(value):
            if opt in device and not CORE.testing_mode:
                raise cv.Invalid(
                    f"The uart {opt} is used both by {name} and {device[opt]}, "
                    f"but can only be used by one. Please create a new uart bus for {name}."
                )
            device[opt] = name
            return value

        return validator

    def validate_data_bits(value):
        if value != data_bits:
            raise cv.Invalid(
                f"Component {name} requires {data_bits} data bits for the uart referenced by {uart_bus}"
            )
        return value

    def validate_parity(value):
        if value != parity:
            raise cv.Invalid(
                f"Component {name} requires parity {parity} for the uart referenced by {uart_bus}"
            )
        return value

    def validate_stop_bits(value):
        if value != stop_bits:
            raise cv.Invalid(
                f"Component {name} requires {stop_bits} stop bits for the uart referenced by {uart_bus}"
            )
        return value

    def validate_hub(hub_config):
        hub_schema = {}
        uart_id = hub_config[CONF_ID]
        uart_id_type_str = str(uart_id.type)
        devices = fv.full_config.get().data.setdefault(KEY_UART_DEVICES, {})
        device = devices.setdefault(uart_id, {})

        if require_tx and uart_id_type_str in NATIVE_UART_CLASSES:
            hub_schema[
                cv.Required(
                    CONF_TX_PIN,
                    msg=f"Component {name} requires uart referenced by {uart_bus} to declare a tx_pin",
                )
            ] = validate_pin(CONF_TX_PIN, device)
        if require_rx and uart_id_type_str in NATIVE_UART_CLASSES:
            hub_schema[
                cv.Required(
                    CONF_RX_PIN,
                    msg=f"Component {name} requires uart referenced by {uart_bus} to declare a rx_pin",
                )
            ] = validate_pin(CONF_RX_PIN, device)
        if baud_rate is not None:
            hub_schema[cv.Required(CONF_BAUD_RATE)] = validate_baud_rate
        if data_bits is not None:
            hub_schema[cv.Required(CONF_DATA_BITS)] = validate_data_bits
        if parity is not None:
            hub_schema[cv.Required(CONF_PARITY)] = validate_parity
        if stop_bits is not None:
            hub_schema[cv.Required(CONF_STOP_BITS)] = validate_stop_bits
        return cv.Schema(hub_schema, extra=cv.ALLOW_EXTRA)(hub_config)

    return cv.Schema(
        {cv.Required(uart_bus): fv.id_declaration_match_schema(validate_hub)},
        extra=cv.ALLOW_EXTRA,
    )


async def register_uart_device(var, config):
    """Register a UART device, setting up all the internal values.

    This is a coroutine, you need to await it with an 'await' expression!
    """
    parent = await cg.get_variable(config[CONF_UART_ID])
    cg.add(var.set_uart_parent(parent))


@automation.register_action(
    "uart.write",
    UARTWriteAction,
    cv.maybe_simple_value(
        {
            cv.GenerateID(): cv.use_id(UARTComponent),
            cv.Required(CONF_DATA): cv.templatable(validate_raw_data),
        },
        key=CONF_DATA,
    ),
    synchronous=True,
)
async def uart_write_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    data = config[CONF_DATA]
    if isinstance(data, bytes):
        data = list(data)

    if cg.is_template(data):
        templ = await cg.templatable(data, args, cg.std_vector.template(cg.uint8))
        cg.add(var.set_data_template(templ))
    else:
        # Generate static array in flash to avoid RAM copy
        arr_id = ID(f"{action_id}_data", is_declaration=True, type=cg.uint8)
        arr = cg.static_const_array(arr_id, cg.ArrayInitializer(*data))
        cg.add(var.set_data_static(arr, len(data)))
    return var


@coroutine_with_priority(CoroPriority.FINAL)
async def final_step():
    """Final code generation step to configure optional UART features."""
    if (CORE.is_esp32 or CORE.is_esp8266) and CORE.has_networking:
        # Wake-on-RX is essentially free (just an ISR function pointer
        # registration on ESP32, an inline flag set on ESP8266 software
        # serial) — enable by default to reduce RX buffer overflow risk by
        # waking the main loop immediately when data arrives.
        cg.add_define("USE_UART_WAKE_LOOP_ON_RX")


_platform_filter = filter_source_files_from_platform(
    {
        "uart_component_esp_idf.cpp": {
            PlatformFramework.ESP32_IDF,
            PlatformFramework.ESP32_ARDUINO,
        },
        "uart_component_esp8266.cpp": {PlatformFramework.ESP8266_ARDUINO},
        "uart_component_host.cpp": {PlatformFramework.HOST_NATIVE},
        "uart_component_rp2.cpp": {PlatformFramework.RP2_ARDUINO},
        "uart_component_libretiny.cpp": {
            PlatformFramework.BK72XX_ARDUINO,
            PlatformFramework.RTL87XX_ARDUINO,
            PlatformFramework.LN882X_ARDUINO,
        },
    }
)

# uart_debugger.cpp is fully #ifdef'd on USE_UART_DEBUGGER, set only when a
# debug block is configured.
_define_filter = filter_source_files_from_defines(
    {"uart_debugger.cpp": "USE_UART_DEBUGGER"}
)


def FILTER_SOURCE_FILES() -> list[str]:
    return _platform_filter() + _define_filter()
