# intelligent-pump
Aadaptive IoT sump pump controller

# Adaptive Sump Pump Controller & Monitoring System

An intelligent IoT system that prevents pump burnout and reduces power costs through adaptive timing control and comprehensive data logging.

## The Problem

My basement sump pump has an internal float switch that often runs longer than necessary, leading to:
- **Risk of pump burnout** from excessive runtime
- **High power costs** from inefficient cycling
- **Limited control** - no external float switch or manual override
- **Expensive alternative** - replacing the undersized sump pit would cost thousands

The pump needed intelligent control that adapts to actual water conditions without requiring hardware modifications.

## The Solution

A Raspberry Pi-based control system that:
- Monitors pump power consumption in real-time to detect actual working time
- Implements an **adaptive timing algorithm** that adjusts cycle intervals based on performance
- Integrates weather data to predict heavy rain periods
- Logs all activity for analysis and future improvements
- Provides SSH-accessible terminal interface for manual control

**Target Performance:** 8-15 seconds of actual pumping per cycle (the "sweet spot")

## System Architecture

### Hardware
- **Raspberry Pi Zero W** - Controller and data processor
- **Smart Plug with API** (ROWI) - Power monitoring and relay control
- **Sump Pump** - Standard 240V pump with internal float switch

### Software Stack
- **Python 3** - Main control script with async operations
- **n8n** - Workflow automation (pulls logs via SSH every hour)
- **NocoDB** - Data storage (pump logs, weather data, forecasts)
- **OpenWeather API** - Current conditions and 5-day rain forecast

### Data Flow
```
Sump Pump → Smart Plug (power monitoring)
           ↓
    Raspberry Pi (adaptive control)
           ↓
    Local CSV logs + Python script
           ↓
    n8n (SSH retrieval every hour)
           ↓
    NocoDB (3 tables: sump logs, current weather, rain forecast)
           ↓
    AI Data Cache (prepared for future automation)
```

## How It Works

### 1. Power Monitoring & Working Time Detection

The system samples power consumption every 0.5 seconds during pump operation to distinguish between actual pumping and idle running:

```python
# Power monitoring thresholds
WORKING_POWER_THRESHOLD = 200     # > 200W = pump working
IDLE_POWER_THRESHOLD = 100        # < 100W = pump idle/off

async def monitor_pump_performance(self, duration):
    """Monitor pump during ON cycle to measure actual working time"""
    working_time = 0
    power_readings = []
    
    while time.time() - start_time < duration:
        power_data = await self.get_power_data()
        if power_data:
            power_readings.append(power_data)
            
            # Check if pump is actually working (moving water)
            if power_data['power_w'] > WORKING_POWER_THRESHOLD:
                working_time += 0.5
        
        await asyncio.sleep(0.5)
    
    return {
        'working_time': working_time,
        'avg_power': avg_power,
        'max_power': max_power
    }
```

### 2. Adaptive Timing Algorithm

The system adjusts wait times based on actual pump performance, targeting 8-15 seconds of working time per cycle:

```python
# Sweet spot: 8-15 seconds working time per cycle
SHORT_RUN_THRESHOLD = 8           # < 8s = increase wait
OPTIMAL_RUN_THRESHOLD = 15        # > 15s = decrease wait

def calculate_dynamic_off_time(self, performance_data):
    working_time = performance_data['working_time']
    
    if working_time < 8:  # Not enough work - wait longer
        adjustment_factor = 2.0
        reason = "insufficient working time"
        
    elif working_time <= 15:  # Sweet spot - maintain interval
        adjustment_factor = 1.0
        reason = "optimal working time - maintaining interval"
        
    elif working_time <= 30:  # Too much work - check more often
        adjustment_factor = 0.7
        reason = "excessive working time"
        
    else:  # HEAVY LOAD - return to 5-minute baseline
        new_off_time = 300
        reason = "HEAVY LOAD - returning to 5-minute baseline"
    
    new_off_time = self.current_off_time * adjustment_factor
```

**Why this works:**
- **<8 seconds:** Not much water to pump → wait longer (2x multiplier)
- **8-15 seconds:** Perfect amount of work → maintain current interval
- **15-30 seconds:** More water than expected → check sooner (0.7x multiplier)
- **>30 seconds:** Heavy load detected → reset to safe 5-minute baseline

### 3. Weather-Aware Adjustments

The system integrates real-time weather data to anticipate increased water flow:

```python
# Weather factors
LIGHT_RAIN_FACTOR = 0.7           # 30% reduction in wait time
HEAVY_RAIN_FACTOR = 0.5           # 50% reduction in wait time
HEAVY_RAIN_THRESHOLD = 2.5        # mm/hr

# Apply weather adjustments
if self.weather_data:
    rain_1h = self.weather_data['rain_1h']
    
    if rain_1h > HEAVY_RAIN_THRESHOLD:
        weather_factor = HEAVY_RAIN_FACTOR  # Check 2x more often
    else:
        weather_factor = LIGHT_RAIN_FACTOR  # Check 1.4x more often
    
    new_off_time *= weather_factor
```

### 4. Manual Override System

SSH-accessible terminal menu provides real-time control:

```bash
# View logs from past 24 hours
sudo journalctl -u intelligent-sump.service --since "24 hours ago"

# Set custom wait time
python3 intelligent_sump_controller.py wait 10  # 10 minutes

# Trigger immediate pump cycle
python3 intelligent_sump_controller.py pump_now

# Return to adaptive mode
python3 intelligent_sump_controller.py normal
```

Terminal menu (`pump-menu.sh`) provides interactive access to:
- View logs from past 24 hours
- Restart controller service
- Set pump wait time
- Exit

## Database Schema

### Sump Logs Table (NocoDB)
- Date, Time
- Working time (actual pumping duration)
- Average power usage
- Max power draw
- Next cycle time
- Next cycle date

### Current Weather Table
- Location
- Temperature
- Conditions
- Precipitation (mm/hr)
- Timestamp

### Rain Forecast Table
- 5-day forecast
- Expected precipitation
- Temperature trends
- Timestamp

### AI Data Cache
- Aggregated data from all sources
- Prepared for AI-driven decision making
- Accessible via chat interface (separate workflow)

## Key Features

✅ **Real-time power monitoring** - Detects actual pump work vs idle running  
✅ **Adaptive timing algorithm** - Self-adjusts based on water flow patterns  
✅ **Weather integration** - Anticipates heavy rain periods  
✅ **Historical data tracking** - CSV logs + cloud database  
✅ **SSH terminal interface** - Manual override and log viewing  
✅ **Health check notifications** - Webhooks for cycle completion/errors  
✅ **AI-ready data pipeline** - Prepared for future automation  

## Results & Impact

**Before intelligent control:**
- Pump ran for 30-45 seconds per cycle (excessive)
- Fixed 7-minute intervals regardless of conditions
- High power consumption
- Risk of premature failure

**After intelligent control:**
- Targets 8-15 seconds of actual work per cycle
- Dynamic intervals (5 minutes to 24 hours based on conditions)
- Reduced power consumption by ~40%
- Avoided $3,000+ sump pit reconstruction

## Tech Stack

| Component | Technology |
|-----------|------------|
| Controller | Raspberry Pi Zero W |
| Language | Python 3 (async/await) |
| Smart Plug | ROWI (API-enabled power monitoring) |
| Workflow Automation | n8n (self-hosted) |
| Database | NocoDB (3 tables) |
| Weather Data | OpenWeather API |
| Service Management | systemd |
| Data Format | CSV + JSON config |

## Installation & Setup

### Prerequisites
- Raspberry Pi (Zero W or newer) with Raspbian/Ubuntu
- Smart plug with API access (ROWI or equivalent)
- Python 3.8+
- n8n instance (optional, for cloud logging)
- NocoDB instance (optional, for database)

### Basic Setup

1. **Install dependencies:**
```bash
pip3 install aiohttp --break-system-packages
```

2. **Configure the script:**
```python
ROWI_IP = "192.168.x.x"  # Your smart plug IP
PUMP_ON_TIME = 45         # Fixed ON duration
BASE_OFF_TIME = 420       # Starting OFF time (7 min)
```

3. **Set up systemd service:**
```bash
sudo cp intelligent-sump.service /etc/systemd/system/
sudo systemctl enable intelligent-sump.service
sudo systemctl start intelligent-sump.service
```

4. **Install terminal menu:**
```bash
cp pump-menu.sh ~/
chmod +x ~/pump-menu.sh
echo "alias pump-menu='~/pump-menu.sh'" >> ~/.bashrc
```

### Configuration File
The system maintains state in `~/sump_config.json`:
```json
{
  "current_off_time": 420,
  "manual_override": null,
  "healthcheck_url": "https://hc-ping.com/your-id",
  "weather_api_key": "your_openweather_key",
  "location": "YourCity,Country"
}
```

## Future Enhancements

### Planned Features
- **AI-driven timing decisions** - Let AI analyze patterns and make adjustments
- **Pre-storm automation** - Webhook triggers for automatic pump resets before heavy rain
- **Predictive maintenance** - Detect performance degradation before failure
- **Extended weather analysis** - Multi-day pattern recognition
- **Mobile notifications** - Push alerts for anomalies or failures
- **Web dashboard** - Real-time monitoring and control interface

### Technical Debt
- Add unit tests for adaptive algorithm
- Implement graceful degradation if weather API fails
- Add backup power monitoring method
- Create Docker container for easier deployment

## Learning Outcomes

This project taught me:
- **IoT device control** - Real-time monitoring and automation
- **Adaptive algorithms** - Self-tuning systems based on performance feedback
- **Async Python** - Non-blocking operations for responsive control
- **API integration** - Smart plugs, weather data, webhooks
- **Data pipeline design** - From sensor → log → database → AI
- **System reliability** - Health checks, error handling, graceful shutdown
- **AI-augmented development** - Using AI as a coding partner to build complex systems

## Development Approach

**I learn by reading and modifying code, not writing from scratch.** This project was built using:
- AI-generated starter code (ChatGPT/Claude)
- Iterative debugging and refinement
- Real-world testing and adjustment
- Documentation of what works

This "read and modify" approach is a valid and increasingly valuable development methodology.

## Project Status

**Current:** Fully operational, running 24/7 since deployment  
**Reliability:** 99%+ uptime, handles power outages gracefully  
**Data Collection:** Several months of logs for analysis  
**Next Steps:** Implement AI-driven timing decisions

---

## About This Project

Built to solve a real problem: preventing pump failure while avoiding expensive sump pit reconstruction. Demonstrates practical IoT development, adaptive algorithms, and AI-augmented coding skills.

**Technologies:** Python, Raspberry Pi, n8n, NocoDB, REST APIs, systemd, async programming

**Skills Demonstrated:** Problem-solving, system design, API integration, data pipeline architecture, real-world deployment

---

*This is a living project - improvements and features are added based on observed behavior and new requirements.*
