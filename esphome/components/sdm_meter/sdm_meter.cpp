#include "sdm_meter.h"
#include "sdm_meter_registers.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <algorithm>

namespace esphome::sdm_meter {

static const char *const TAG = "sdm_meter";

// Fixed cost of an extra read request, in byte times: an 8-byte request frame, 5 bytes of response
// framing, and two 3.5-character inter-frame gaps. The hub's turnaround idle time is added on top
// at runtime.
static const uint16_t SPLIT_OVERHEAD_BYTES = 20;

void SDMMeter::on_read_input_registers(uint16_t start_address, std::span<const uint16_t> registers,
                                       modbus::ResponseStatus status) {
  if (!modbus::succeeded(status))
    return;  // the hub already logs exception responses

  // Publish a sensor if both of its registers are in this response; skipping absent registers keeps
  // this correct for any read range, so the poll may be split into multiple requests.
  auto publish = [&](uint16_t reg, sensor::Sensor *sensor) {
    if (sensor == nullptr || reg < start_address)
      return;
    size_t offset = reg - start_address;
    if (offset + 2 > registers.size())
      return;
    auto value =
        modbus::helpers::registers_to_number(registers.data() + offset, 2, modbus::helpers::SensorValueType::FP32);
    if (value.has_value())
      sensor->publish_state(bit_cast<float>(static_cast<uint32_t>(*value)));
  };

  for (uint8_t i = 0; i < 3; i++) {
    auto &phase = this->phases_[i];
    if (!phase.setup)
      continue;
    publish(SDM_PHASE_1_VOLTAGE + i * 2, phase.voltage_sensor_);
    publish(SDM_PHASE_1_CURRENT + i * 2, phase.current_sensor_);
    publish(SDM_PHASE_1_ACTIVE_POWER + i * 2, phase.active_power_sensor_);
    publish(SDM_PHASE_1_APPARENT_POWER + i * 2, phase.apparent_power_sensor_);
    publish(SDM_PHASE_1_REACTIVE_POWER + i * 2, phase.reactive_power_sensor_);
    publish(SDM_PHASE_1_POWER_FACTOR + i * 2, phase.power_factor_sensor_);
    publish(SDM_PHASE_1_ANGLE + i * 2, phase.phase_angle_sensor_);
  }

  publish(SDM_TOTAL_SYSTEM_POWER, this->total_power_sensor_);
  publish(SDM_FREQUENCY, this->frequency_sensor_);
  publish(SDM_IMPORT_ACTIVE_ENERGY, this->import_active_energy_sensor_);
  publish(SDM_EXPORT_ACTIVE_ENERGY, this->export_active_energy_sensor_);
  publish(SDM_IMPORT_REACTIVE_ENERGY, this->import_reactive_energy_sensor_);
  publish(SDM_EXPORT_REACTIVE_ENERGY, this->export_reactive_energy_sensor_);
}

void SDMMeter::update() {
  // Collect the start register of every configured sensor (each value spans 2 registers).
  StaticVector<uint16_t, 27> regs;  // 7 phase sensors x 3 phases + 6 totals
  auto add = [&](sensor::Sensor *sensor, uint16_t reg) {
    if (sensor != nullptr)
      regs.push_back(reg);
  };
  for (uint8_t i = 0; i < 3; i++) {
    auto &phase = this->phases_[i];
    add(phase.voltage_sensor_, SDM_PHASE_1_VOLTAGE + i * 2);
    add(phase.current_sensor_, SDM_PHASE_1_CURRENT + i * 2);
    add(phase.active_power_sensor_, SDM_PHASE_1_ACTIVE_POWER + i * 2);
    add(phase.apparent_power_sensor_, SDM_PHASE_1_APPARENT_POWER + i * 2);
    add(phase.reactive_power_sensor_, SDM_PHASE_1_REACTIVE_POWER + i * 2);
    add(phase.power_factor_sensor_, SDM_PHASE_1_POWER_FACTOR + i * 2);
    add(phase.phase_angle_sensor_, SDM_PHASE_1_ANGLE + i * 2);
  }
  add(this->total_power_sensor_, SDM_TOTAL_SYSTEM_POWER);
  add(this->frequency_sensor_, SDM_FREQUENCY);
  add(this->import_active_energy_sensor_, SDM_IMPORT_ACTIVE_ENERGY);
  add(this->export_active_energy_sensor_, SDM_EXPORT_ACTIVE_ENERGY);
  add(this->import_reactive_energy_sensor_, SDM_IMPORT_REACTIVE_ENERGY);
  add(this->export_reactive_energy_sensor_, SDM_EXPORT_REACTIVE_ENERGY);
  if (regs.empty())
    return;

  // Splitting only pays off when the skipped registers cost more than the extra request: its fixed
  // framing overhead plus the turnaround idle the hub enforces before every transmit, against
  // 2 bytes per unneeded register read. With the default 600 ms turnaround a split never pays off
  // within this register map; it kicks in when the hub is tuned for a faster bus.
  const uint32_t split_gap = (SPLIT_OVERHEAD_BYTES + this->parent_->get_turnaround_byte_times()) / 2;

  // Queue one read per cluster of registers, starting a new request only where the gap to the next
  // needed register exceeds the breakeven point.
  std::sort(regs.begin(), regs.end());
  uint16_t start = regs[0];
  uint16_t end = regs[0] + 2;
  for (uint16_t reg : regs) {
    if (reg > end + split_gap) {
      this->read_input_registers(start, end - start);
      start = reg;
    }
    end = reg + 2;
  }
  this->read_input_registers(start, end - start);
}
void SDMMeter::dump_config() {
  ESP_LOGCONFIG(TAG,
                "SDM Meter:\n"
                "  Address: 0x%02X",
                this->address_);
  for (uint8_t i = 0; i < 3; i++) {
    auto phase = this->phases_[i];
    if (!phase.setup)
      continue;
    ESP_LOGCONFIG(TAG, "  Phase %c", i + 'A');
    LOG_SENSOR("    ", "Voltage", phase.voltage_sensor_);
    LOG_SENSOR("    ", "Current", phase.current_sensor_);
    LOG_SENSOR("    ", "Active Power", phase.active_power_sensor_);
    LOG_SENSOR("    ", "Apparent Power", phase.apparent_power_sensor_);
    LOG_SENSOR("    ", "Reactive Power", phase.reactive_power_sensor_);
    LOG_SENSOR("    ", "Power Factor", phase.power_factor_sensor_);
    LOG_SENSOR("    ", "Phase Angle", phase.phase_angle_sensor_);
  }
  LOG_SENSOR("  ", "Total Power", this->total_power_sensor_);
  LOG_SENSOR("  ", "Frequency", this->frequency_sensor_);
  LOG_SENSOR("  ", "Import Active Energy", this->import_active_energy_sensor_);
  LOG_SENSOR("  ", "Export Active Energy", this->export_active_energy_sensor_);
  LOG_SENSOR("  ", "Import Reactive Energy", this->import_reactive_energy_sensor_);
  LOG_SENSOR("  ", "Export Reactive Energy", this->export_reactive_energy_sensor_);
}

}  // namespace esphome::sdm_meter
