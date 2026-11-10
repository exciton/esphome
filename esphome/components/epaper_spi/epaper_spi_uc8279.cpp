#include "epaper_spi_uc8279.h"

#include <algorithm>
#include <cstring>

#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

namespace esphome::epaper_spi {
ESPHOME_LOG_TAG(TAG, "epaper_spi.uc8279");

static constexpr uint8_t CMD_PSR = 0x00;
static constexpr uint8_t CMD_PFS = 0x03;
static constexpr uint8_t CMD_GATE_SCAN = 0xE1;

static constexpr uint8_t PSR0_OTP_LUT = 0x17;
static constexpr uint8_t PSR1 = 0x4D;
static constexpr uint8_t PFS = 0x20;
static constexpr uint8_t GATE_SCAN = 0x02;
static constexpr uint8_t CCSET = 0x02;
static constexpr uint8_t TSSET_FULL = 0x1E;
static constexpr uint8_t TSSET_PARTIAL = 0x5A;
static constexpr uint8_t CDI_FULL = 0x97;
static constexpr uint8_t CDI_PARTIAL = 0xD7;

static constexpr uint16_t RESET_SETTLE_MS = 50;
static constexpr uint16_t BUSY_ASSERT_MS = 10;
static constexpr uint16_t REFRESH_START_MS = 50;

bool EPaperUC8279::reset() {
  // A partial update follows one that left the controller configured, in standby, with DTM1 holding the image
  if (this->partial_)
    return true;
  if (!EPaperBase::reset())
    return false;
  this->next_delay_ = RESET_SETTLE_MS;
  return true;
}

bool EPaperUC8279::initialise(bool partial) {
  if (partial)
    return true;
  return EPaperBase::initialise(partial);
}

void EPaperUC8279::write_gate_(size_t gate, bool white) {
  const size_t row_width = this->row_width_;
  if (white || gate < this->offset_height_) {
    uint8_t white_bytes[MAX_TRANSFER_SIZE];
    memset(white_bytes, 0xFF, sizeof(white_bytes));
    for (size_t written = 0; written != row_width;) {
      const size_t length = std::min(row_width - written, MAX_TRANSFER_SIZE);
      this->write_array(white_bytes, length);
      written += length;
    }
    return;
  }
  size_t index = (gate - this->offset_height_) * row_width;
  for (size_t remaining = row_width; remaining != 0;) {
    size_t length;
    const uint8_t *data = this->buffer_.get_span(index, length);
    length = std::min(length, remaining);
    this->write_array(data, length);
    index += length;
    remaining -= length;
  }
}

// One plane of one gate's row per step, time-sliced; `base` is the value of current_data_index_ at its first gate
bool HOT EPaperUC8279::write_plane_(uint8_t command, bool white, size_t base) {
  const uint32_t start_time = millis();
  const size_t gates = this->gate_count_();
  if (this->current_data_index_ == base)
    this->command(command);
  this->start_data_();
  while (this->current_data_index_ - base != gates) {
    this->write_gate_(this->current_data_index_ - base, white);
    ++this->current_data_index_;
    if (this->current_data_index_ - base != gates && millis() - start_time > MAX_TRANSFER_TIME) {
      this->disable();
      return false;
    }
  }
  this->disable();
  return true;
}

// DTM2 gets the new image; a full refresh also writes a white DTM1. After a wake that lost the panel's RAM
// the first partial seeds DTM1 with the restored previous frame instead, so only changed pixels transition.
bool HOT EPaperUC8279::transfer_data() {
  const size_t gates = this->gate_count_();
  if (this->current_data_index_ < gates) {
    if (!this->write_plane_(CMD_DTM2, false, 0))
      return false;
    // Start the second plane on the next pass, so this one stays within its time slice
    if (!this->partial_ || this->restore_previous_)
      return false;
  }
  if (!this->partial_) {
    if (!this->write_plane_(CMD_DTM1, true, gates))
      return false;
  } else if (this->restore_previous_) {
    if (!this->write_plane_restored_(CMD_DTM1, gates))
      return false;
    this->free_restored_frame_();
  }
  this->current_data_index_ = 0;
  return true;
}

void EPaperUC8279::power_on() {
  ESP_LOGV(TAG, "Power on");
  this->cmd_data(CMD_CDI, {this->partial_ ? CDI_PARTIAL : CDI_FULL});
  this->cmd_data(CMD_CCSET, {CCSET});
  this->cmd_data(CMD_TSSET, {this->partial_ ? TSSET_PARTIAL : TSSET_FULL});
  if (this->partial_) {
    this->cmd_data(CMD_PFS, {PFS});
    this->cmd_data(CMD_GATE_SCAN, {GATE_SCAN});
  }
  this->command(CMD_POWER_ON);
  this->next_delay_ = BUSY_ASSERT_MS;
}

void EPaperUC8279::refresh_screen(bool partial) {
  ESP_LOGV(TAG, "Refresh screen (%s)", partial ? LOG_STR_LITERAL("partial") : LOG_STR_LITERAL("full"));
  if (partial)
    this->set_partial_window_(0, this->width_ - 1, this->offset_height_, this->gate_count_() - 1);
  // Power-on reloads the panel's stored settings, so the panel setting only takes when written after it
  this->cmd_data(CMD_PSR, {PSR0_OTP_LUT, PSR1});
  this->command(CMD_REFRESH);
  this->next_delay_ = REFRESH_START_MS;
}

bool HOT EPaperUC8279::after_refresh() {
  // While partial updates are in use, DTM1 holds the image on the panel for the next partial comparison
  if (!this->is_using_partial_update_())
    return true;
  if (this->current_data_index_ == 0 && this->partial_)
    this->command(CMD_PARTIAL_OUT);
  if (!this->write_plane_(CMD_DTM1, false, 0))
    return false;
  this->current_data_index_ = 0;
  return true;
}

void EPaperUC8279::power_off() {
  this->command(CMD_POWER_OFF);
  this->next_delay_ = BUSY_ASSERT_MS;
}

void EPaperUC8279::setup() {
  EPaperBase::setup();
  // buffer_ is ready, so an image kept in RTC memory can be restored into the heap and seeded on the first update
  this->try_restore_image_();
}

bool EPaperUC8279::accept_restored_frame(const uint8_t *data, size_t length) {
  this->free_restored_frame_();
  RAMAllocator<uint8_t> allocator;
  this->restored_frame_ = allocator.allocate(length);
  if (this->restored_frame_ == nullptr)
    return false;
  memcpy(this->restored_frame_, data, length);
  return true;
}

void EPaperUC8279::free_restored_frame_() {
  if (this->restored_frame_ == nullptr)
    return;
  RAMAllocator<uint8_t> allocator;
  allocator.deallocate(this->restored_frame_, this->buffer_length_);
  this->restored_frame_ = nullptr;
}

void EPaperUC8279::write_gate_restored_(size_t gate) {
  const size_t row_width = this->row_width_;
  const uint8_t *row = nullptr;
  uint8_t white_bytes[MAX_TRANSFER_SIZE];
  if (gate < this->offset_height_) {
    memset(white_bytes, 0xFF, sizeof(white_bytes));
  } else {
    row = this->restored_frame_ + (gate - this->offset_height_) * row_width;
  }
  for (size_t written = 0; written != row_width;) {
    const size_t length = std::min(row_width - written, MAX_TRANSFER_SIZE);
    this->write_array(row != nullptr ? row + written : white_bytes, length);
    written += length;
  }
}

bool HOT EPaperUC8279::write_plane_restored_(uint8_t command, size_t base) {
  const uint32_t start_time = millis();
  const size_t gates = this->gate_count_();
  if (this->current_data_index_ == base)
    this->command(command);
  this->start_data_();
  while (this->current_data_index_ - base != gates) {
    this->write_gate_restored_(this->current_data_index_ - base);
    ++this->current_data_index_;
    if (this->current_data_index_ - base != gates && millis() - start_time > MAX_TRANSFER_TIME) {
      this->disable();
      return false;
    }
  }
  this->disable();
  return true;
}

}  // namespace esphome::epaper_spi
