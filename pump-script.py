#!/usr/bin/env python3
"""
Intelligent ROWI Sump Pump Controller with Power Monitoring
Adapts timing based on actual pump performance and weather conditions
Sweet spot: 8-15 seconds working time per cycle
"""

import asyncio
import aiohttp
import json
import time
import logging
import csv
from datetime import datetime, timedelta
from pathlib import Path
import signal
import sys

# Configuration Constants
ROWI_IP = ""
PUMP_ON_TIME = 45                 # Fixed pump ON duration (seconds)
BASE_OFF_TIME = 420               # Base OFF time (7 minutes = 420 seconds)
MIN_OFF_TIME = 300                # Minimum OFF time (5 minutes)
MAX_OFF_TIME = 86400              # Maximum OFF time (24 hours)

# Power monitoring thresholds
WORKING_POWER_THRESHOLD = 200     # > 200W = pump working
IDLE_POWER_THRESHOLD = 100        # < 100W = pump idle/off
MIN_WORKING_TIME = 3              # Minimum seconds to count as "working"

# Runtime adaptation thresholds - SWEET SPOT: 8-15 seconds
SHORT_RUN_THRESHOLD = 8           # < 8s actual working = increase wait
OPTIMAL_RUN_THRESHOLD = 15        # > 15s actual working = decrease wait

# Weather factors
LIGHT_RAIN_FACTOR = 0.7           # 30% reduction in wait time
HEAVY_RAIN_FACTOR = 0.5           # 50% reduction in wait time
HEAVY_RAIN_THRESHOLD = 2.5        # mm/hr

# File locations
CSV_FILE = Path.home() / ""

class IntelligentPumpController:
    def __init__(self):
        self.cycle_count = 0
        self.current_off_time = BASE_OFF_TIME
        self.manual_override = None
        self.override_command = None
        self.weather_data = None
        self.pump_status = "unknown"
        self.next_cycle_time = None
        self.is_running = False
        self.session = None
        
        # Load configuration
        self.healthcheck_url = ""
        self.weather_api_key = ""
        self.location = ""
        
        self.setup_logging()
        self.setup_signal_handlers()
    
    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(Path.home() / ""),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def setup_signal_handlers(self):
        """Handle graceful shutdown"""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.is_running = False
    
    def load_config(self):
        config_file = Path.home() / "sump_config.json"
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    self.current_off_time = config.get('current_off_time', BASE_OFF_TIME)
                    self.manual_override = config.get('manual_override')
                    self.healthcheck_url = config.get('healthcheck_url', '')
                    self.weather_api_key = config.get('weather_api_key', '')
                    self.location = config.get('location', self.location)
                    self.logger.info(f"Config loaded: off_time={self.current_off_time}s")
            except Exception as e:
                self.logger.error(f"Error loading config: {e}")
    
    def save_config(self):
        config = {
            'current_off_time': self.current_off_time,
            'manual_override': self.manual_override,
            'healthcheck_url': self.healthcheck_url,
            'weather_api_key': self.weather_api_key,
            'location': self.location,
            'last_updated': datetime.now().isoformat()
        }
        try:
            config_file = Path.home() / "sump_config.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving config: {e}")
    
    async def get_weather_data(self):
        """Get current weather conditions"""
        if not self.weather_api_key or self.weather_api_key == "your_api_key":
            return None
        
        url = f"http://api.openweathermap.org/data/2.5/weather?q={self.location}&appid={self.weather_api_key}"
        
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    weather_main = data['weather'][0]['main'].lower()
                    rain_data = data.get('rain', {})
                    rain_1h = rain_data.get('1h', 0)
                    
                    self.weather_data = {
                        'condition': weather_main,
                        'rain_1h': rain_1h,
                        'description': data['weather'][0]['description']
                    }
                    
                    self.logger.info(f"Weather: {self.weather_data['description']}, Rain: {rain_1h}mm/h")
                    return self.weather_data
        except Exception as e:
            self.logger.error(f"Error fetching weather: {e}")
        
        return None
    
    async def control_pump(self, action):
        """Turn pump ON or OFF"""
        url = f"http://{ROWI_IP}/setRelayStatus"
        data = {"data": action}
        
        for attempt in range(3):
            try:
                async with self.session.post(url, json=data, timeout=10) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('rslt') == 'OK':
                            self.pump_status = action
                            self.logger.info(f"Pump {action.upper()} - Success")
                            return True
                        else:
                            self.logger.error(f"Pump {action} failed: {result}")
                    else:
                        self.logger.error(f"HTTP {response.status} when turning pump {action}")
            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        
        return False
    
    async def get_power_data(self):
        """Get current power consumption from ROWI"""
        url = f"http://{ROWI_IP}/getPowerMeterData"
        
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('rslt') == 'OK':
                        power_w = float(result.get('pmom', 0))
                        current_a = float(result.get('imad', 0))
                        voltage_v = float(result.get('volt', 0))
                        
                        return {
                            'power_w': power_w,
                            'current_a': current_a,
                            'voltage_v': voltage_v,
                            'timestamp': datetime.now()
                        }
        except Exception as e:
            self.logger.error(f"Error getting power data: {e}")
        
        return None
    
    async def monitor_pump_performance(self, duration):
        """Monitor pump during ON cycle to measure actual working time"""
        working_time = 0
        power_readings = []
        start_time = time.time()
        
        self.logger.info(f"Monitoring pump performance for {duration}s...")
        
        # Sample power every 0.5 seconds during pump cycle
        while time.time() - start_time < duration:
            power_data = await self.get_power_data()
            if power_data:
                power_readings.append(power_data)
                
                # Check if pump is actually working (moving water)
                if power_data['power_w'] > WORKING_POWER_THRESHOLD:
                    working_time += 0.5
                
                self.logger.debug(f"Power: {power_data['power_w']:.1f}W, Working time: {working_time:.1f}s")
            
            await asyncio.sleep(0.5)
        
        # Calculate statistics
        if power_readings:
            avg_power = sum(r['power_w'] for r in power_readings) / len(power_readings)
            max_power = max(r['power_w'] for r in power_readings)
            min_power = min(r['power_w'] for r in power_readings)
            
            self.logger.info(f"Performance: {working_time:.1f}s working, Avg: {avg_power:.1f}W, Max: {max_power:.1f}W")
            
            return {
                'working_time': working_time,
                'total_time': duration,
                'avg_power': avg_power,
                'max_power': max_power,
                'min_power': min_power,
                'power_readings': power_readings
            }
        
        return {
            'working_time': 0,
            'total_time': duration,
            'avg_power': 0,
            'max_power': 0,
            'min_power': 0,
            'power_readings': []
        }
    
    def calculate_dynamic_off_time(self, performance_data):
        """Calculate next OFF time based on pump performance and weather"""
        working_time = performance_data['working_time']
        
        # Check for manual override first
        if self.manual_override:
            self.logger.info(f"Using manual override: {self.manual_override}s")
            return self.manual_override
        
        # Base adjustment on working time - SWEET SPOT: 8-15 seconds
        if working_time < SHORT_RUN_THRESHOLD:  # <8s - not enough work
            adjustment_factor = 2.0
            reason = f"insufficient working time ({working_time:.1f}s)"
        elif working_time <= OPTIMAL_RUN_THRESHOLD:  # 8-15s - sweet spot
            adjustment_factor = 1.0  # No change
            reason = f"optimal working time ({working_time:.1f}s) - maintaining interval"
        elif working_time <= 30:  # 15-30s - too much work
            adjustment_factor = 0.7
            reason = f"excessive working time ({working_time:.1f}s)"
        else:  # >30s - HEAVY LOAD
            # Return to 5-minute baseline
            new_off_time = 300
            reason = f"HEAVY LOAD ({working_time:.1f}s) - returning to 5-minute baseline"
            
            # Apply weather if present
            if self.weather_data and ('rain' in self.weather_data['condition'] or self.weather_data['rain_1h'] > 0):
                rain_1h = self.weather_data['rain_1h']
                if rain_1h > HEAVY_RAIN_THRESHOLD:
                    new_off_time = int(new_off_time * 0.8)
                    reason += f", heavy rain ({rain_1h}mm/h)"
                else:
                    new_off_time = int(new_off_time * 0.9)
                    reason += f", light rain ({rain_1h}mm/h)"
            
            new_off_time = max(MIN_OFF_TIME, new_off_time)
            self.current_off_time = BASE_OFF_TIME
            self.logger.info(f"Off time: {reason} → {new_off_time:.0f}s")
            return new_off_time
        
        new_off_time = self.current_off_time * adjustment_factor
        
        # Weather adjustments
        weather_factor = 1.0
        weather_reason = ""
        
        if self.weather_data:
            condition = self.weather_data['condition']
            rain_1h = self.weather_data['rain_1h']
            
            if 'rain' in condition or rain_1h > 0:
                if rain_1h > HEAVY_RAIN_THRESHOLD:
                    weather_factor = HEAVY_RAIN_FACTOR
                    weather_reason = f", heavy rain ({rain_1h}mm/h)"
                else:
                    weather_factor = LIGHT_RAIN_FACTOR
                    weather_reason = f", light rain ({rain_1h}mm/h)"
                
                new_off_time *= weather_factor
        
        # Apply limits
        new_off_time = max(MIN_OFF_TIME, min(MAX_OFF_TIME, new_off_time))
        
        self.logger.info(f"Off time: {reason}{weather_reason} → {new_off_time:.0f}s")
        
        return int(new_off_time)
    
    async def send_health_check(self, message=""):
        """Send health check notification"""
        if not self.healthcheck_url:
            return
        
        try:
            async with self.session.post(self.healthcheck_url, data=message, timeout=10) as response:
                if response.status == 200:
                    self.logger.info(f"Health check: {message}")
        except Exception as e:
            self.logger.error(f"Health check error: {e}")
    
    def log_to_csv(self, performance_data, next_off_time, weather_condition="unknown"):
        """Log cycle data to CSV file"""
        try:
            now = datetime.now()
            row_data = {
                'Timestamp': now.isoformat(),
                'Date': now.strftime('%Y-%m-%d'),
                'Time': now.strftime('%H:%M:%S'),
                'Event': 'CYCLE_COMPLETE',
                'Power_W': round(performance_data.get('avg_power', 0), 1),
                'Current_A': round(performance_data.get('avg_power', 0) / 240, 2),
                'Voltage_V': 240,
                'Relay_State': 'OFF',
                'Pump_Working': 'YES' if performance_data.get('working_time', 0) > MIN_WORKING_TIME else 'NO',
                'Runtime_Sec': round(performance_data.get('working_time', 0), 1),
                'Daily_Cycles': self.cycle_count,
                'Daily_Runtime_Sec': round(performance_data.get('working_time', 0), 1),
                'Notes': f"Next wait: {next_off_time//60}min, Weather: {weather_condition}, Max power: {performance_data.get('max_power', 0):.0f}W"
            }
            
            # Write to CSV
            file_exists = CSV_FILE.exists()
            with open(CSV_FILE, 'a', newline='') as csvfile:
                fieldnames = list(row_data.keys())
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                writer.writerow(row_data)
            
            self.logger.info(f"Data logged: {performance_data.get('working_time', 0):.1f}s working, {next_off_time//60}min wait")
            
        except Exception as e:
            self.logger.error(f"Error logging data: {e}")
    
    def check_override_commands(self):
        """Check for manual override commands from file"""
        override_file = Path.home() / "pump_override.txt"
        if override_file.exists():
            try:
                with open(override_file, 'r') as f:
                    command = f.read().strip().lower()
                
                if command == "stop":
                    self.logger.info("OVERRIDE: System stop requested")
                    self.is_running = False
                elif command == "normal":
                    self.manual_override = None
                    self.logger.info("OVERRIDE: Returned to normal operation")
                elif command.startswith("wait"):
                    parts = command.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        minutes = int(parts[1])
                        self.manual_override = minutes * 60
                        self.logger.info(f"OVERRIDE: Set wait time to {minutes} minutes")
                elif command == "pump_now":
                    self.override_command = "pump_now"
                    self.logger.info("OVERRIDE: Immediate pump requested")
                
                override_file.unlink()
                self.save_config()
                
            except Exception as e:
                self.logger.error(f"Error processing override: {e}")
    
    async def run_cycle(self):
        """Execute one complete pump cycle with power monitoring"""
        self.cycle_count += 1
        
        # Get weather data
        await self.get_weather_data()
        
        self.logger.info(f"\n--- Intelligent Cycle {self.cycle_count} ---")
        
        # Turn pump ON
        if not await self.control_pump("on"):
            await self.send_health_check(f"ERROR Cycle {self.cycle_count}: Failed ON")
            return False
        
        await self.send_health_check(f"Cycle {self.cycle_count}: Pump ON - monitoring performance")
        
        # Monitor pump performance during ON cycle
        performance_data = await self.monitor_pump_performance(PUMP_ON_TIME)
        
        # Turn pump OFF
        if not await self.control_pump("off"):
            self.logger.error("Failed to turn pump OFF")
            await self.send_health_check(f"ERROR Cycle {self.cycle_count}: Failed OFF")
            return False
        
        # Calculate next wait time based on performance
        next_off_time = self.calculate_dynamic_off_time(performance_data)
        self.current_off_time = next_off_time
        self.save_config()
        
        # Log data
        weather_desc = self.weather_data.get('description', 'unknown') if self.weather_data else 'unknown'
        self.log_to_csv(performance_data, next_off_time, weather_desc)
        
        # Set next cycle time
        self.next_cycle_time = datetime.now() + timedelta(seconds=next_off_time)
        
        await self.send_health_check(
            f"Cycle {self.cycle_count}: Working {performance_data['working_time']:.1f}s, "
            f"Next: {next_off_time/60:.1f}min"
        )
        
        # Display wait time
        if next_off_time >= 3600:
            time_desc = f"{next_off_time/3600:.1f} hours"
        else:
            time_desc = f"{next_off_time/60:.1f} minutes"
        
        self.logger.info(f"Pump OFF for {time_desc}")
        self.logger.info(f"Next cycle: {self.next_cycle_time.strftime('%H:%M:%S on %Y-%m-%d')}")
        
        # Wait for next cycle (with periodic override checks)
        elapsed = 0
        check_interval = 30
        
        while elapsed < next_off_time and self.is_running:
            await asyncio.sleep(min(check_interval, next_off_time - elapsed))
            elapsed += check_interval
            
            self.check_override_commands()
            
            if self.override_command == "pump_now":
                self.logger.info("Override: Immediate pump cycle requested")
                self.override_command = None
                break
        
        return True
    
    async def run_controller(self):
        """Main controller loop"""
        self.is_running = True
        self.logger.info("🧠 Starting Intelligent ROWI Controller with Power Monitoring")
        self.logger.info(f"Device IP: {ROWI_IP}")
        self.logger.info(f"Timing: {PUMP_ON_TIME}s ON, {BASE_OFF_TIME}s OFF (adaptive)")
        self.logger.info(f"Sweet Spot: {SHORT_RUN_THRESHOLD}-{OPTIMAL_RUN_THRESHOLD}s working time per cycle")
        self.logger.info(f"Power thresholds: Working>{WORKING_POWER_THRESHOLD}W, Idle<{IDLE_POWER_THRESHOLD}W")
        
        # Wait for system startup
        self.logger.info("Waiting 60 seconds for system startup...")
        await asyncio.sleep(60)
        
        # Create session
        self.session = aiohttp.ClientSession()
        
        try:
            while self.is_running:
                try:
                    success = await self.run_cycle()
                    if not success:
                        self.logger.error("Cycle failed, retrying in 5 minutes")
                        await asyncio.sleep(300)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Cycle error: {e}")
                    await self.send_health_check(f"ERROR: {e}")
                    await asyncio.sleep(60)
        
        finally:
            # Shutdown cleanup
            self.logger.info("Turning pump OFF before shutdown...")
            try:
                await self.control_pump("off")
            except:
                pass
            
            if self.session:
                await self.session.close()
    
    def create_override_command(self, command):
        """Create override command file"""
        override_file = Path.home() / "pump_override.txt"
        with open(override_file, 'w') as f:
            f.write(command)
        print(f"Override command '{command}' created")

async def main():
    controller = IntelligentPumpController()
    
    # Handle command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command in ["stop", "normal", "pump_now"]:
            controller.create_override_command(command)
            return
        elif command.startswith("wait"):
            controller.create_override_command(" ".join(sys.argv[1:]))
            return
        elif command == "test":
            # Test mode - single cycle
            controller.session = aiohttp.ClientSession()
            try:
                await controller.run_cycle()
            finally:
                await controller.session.close()
            return
    
    # Normal operation
    try:
        await controller.run_controller()
    except KeyboardInterrupt:
        controller.logger.info("Shutdown requested by user")
    except Exception as e:
        controller.logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
