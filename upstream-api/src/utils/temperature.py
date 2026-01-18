"""
Example script demonstrating how to use the weather module.
This file is kept for reference but the actual weather functionality
has been moved to src/core/weather.py
"""

from src.core.weather import get_water_temperature, get_wave_weather

# Example: Get just the water temperature
water_temp = get_water_temperature()
print(f"Water temperature: {water_temp}°F")

# Example: Get full weather data (water temp, air temp, conditions)
water_temp, air_temp, conditions = get_wave_weather()
print(f"Water: {water_temp}°F, Air: {air_temp}°F, Conditions: {conditions}")

