# Upstream API

Flask API that proxies and caches calendar data from The Wave ticketing API, with history archiving, authentication, and water temperature tracking.

## Project Structure

```
upstream-api/
├── src/                    # Application source code
│   ├── core/              # Core application modules
│   │   ├── auth.py        # Authentication (x-api-key)
│   │   ├── wave_calendar.py # Calendar data fetching and transformation
│   │   ├── history.py     # Historical data management
│   │   ├── scheduler.py   # Daily archive scheduler
│   │   ├── weather.py     # Weather and temperature data fetching
│   │   ├── water_temp_db.py # SQLite database for water temperature history
│   │   └── performance_temperature.py # Adds temperature data to performances
│   └── utils/             # Utility modules
│       ├── temperature.py # Example script using weather module
│       └── emulate_upstream.py # Upstream API emulation utility
├── config/                 # Configuration files
│   ├── config.yaml.example # Example config (template)
│   └── config.yaml        # Actual config (gitignored)
├── data/                   # Data files
│   ├── response.json      # Test data for test mode
│   ├── history/           # Historical API responses (YYYY-MM-DD.json)
│   └── water_temperature.db # SQLite database for temperature history
├── scripts/                # Utility scripts
│   └── modify_availability.py # Script to modify test data
├── main.py                # Flask application entry point
├── latest.csv             # Temperature forecast data (predicted_water_temp)
├── Dockerfile             # Docker configuration
├── pyproject.toml         # Python project configuration
└── README.md              # This file
```

## Features

- **Calendar API Proxy**: Caches and proxies calendar data from upstream API
- **Historical Data**: Archives daily responses and serves historical data for past dates
- **Test Mode**: Uses dummy data from `data/response.json` for development
- **Authentication**: x-api-key header authentication (configurable)
- **Weather & Temperature**: Scrapes and caches weather data (water temp, air temp, conditions)
- **Temperature History**: Stores water temperature readings in SQLite database
- **Performance Temperature**: Automatically adds water temperature to calendar performances:
  - Past performances: Historical temperature from database
  - Current performances: Live temperature
  - Future performances (next 7 days): Predicted temperature from forecast

## Configuration

### API Keys

Create `config/config.yaml` from `config/config.yaml.example`:

```yaml
api_keys:
  - your-api-key-here
  - another-api-key-if-needed
```

Or set the `API_KEYS` environment variable (comma-separated):
```bash
export API_KEYS="key1,key2,key3"
```

To disable authentication (development only):
```bash
export DISABLE_API_AUTH=true
```

### Environment Variables

- `TEST_MODE`: Enable test mode (uses `data/response.json`)
- `DISABLE_API_AUTH`: Disable authentication (default: false)
- `API_KEYS`: Comma-separated list of API keys (alternative to config.yaml)
- `CACHE_TTL_SECONDS`: Cache TTL in seconds (default: 600)
- `FORECAST_RELOAD_HOUR`: Hour to reload forecast data daily, 0-23 (default: 1 = 01:00)
- `HISTORICAL_TEMP_CACHE_SIZE`: LRU cache size for historical temperature lookups (default: 256)
- `DB_TIMEOUT`: SQLite database lock timeout in seconds (default: 30.0)
- `PORT`: Server port (default: 5000)

## Running

### Development

```bash
python main.py
```

### Production (Docker)

```bash
docker build -t upstream-api .
docker run -p 5000:5000 upstream-api
```

## API Endpoints

### Calendar Endpoints
- `GET /calendar?dateFrom=YYYY-MM-DD&numberOfDays=N` - Get calendar data

### Weather Endpoints
- `GET /water-temperature` - Get current water temperature (cached until next hour)
- `GET /wave-weather` - Get full weather data (water temp, air temp, conditions)

## Data Flow

### Temperature in Performances

The `/calendar` endpoint automatically enriches performance data with temperature:

1. **Past performances**: Retrieves historical temperature from `water_temperature.db` (within ±2 hours of performance time)
2. **Current performances**: Fetches live temperature from weather scraper
3. **Future performances** (next 7 days): Uses predicted temperature from `latest.csv` forecast file

Response fields added to performances:
- `waterTemperature`: Actual temperature (past/current performances)
- `predictedWaterTemp`: Forecast temperature (future performances)