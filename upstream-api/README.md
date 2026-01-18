# Upstream API

Flask API that proxies and caches calendar data from The Wave ticketing API, with history archiving and authentication.

## Project Structure

```
upstream-api/
├── src/                    # Application source code
│   ├── core/              # Core application modules
│   │   ├── auth.py        # Authentication (x-api-key)
│   │   ├── wave_calendar.py # Calendar data fetching and transformation
│   │   ├── history.py     # Historical data management
│   │   ├── scheduler.py   # Daily archive scheduler
│   │   └── weather.py     # Weather and temperature data fetching
│   └── utils/             # Utility modules
│       └── temperature.py # Example script using weather module
├── config/                 # Configuration files
│   ├── config.yaml.example # Example config (template)
│   └── config.yaml        # Actual config (gitignored)
├── data/                   # Data files
│   ├── response.json      # Test data for test mode
│   └── history/           # Historical API responses (YYYY-MM-DD.json)
├── scripts/                # Utility scripts
│   └── modify_availability.py # Script to modify test data
├── main.py                # Flask application entry point
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
- `GET /water-temperature` - Get current water temperature
- `GET /wave-weather` - Get full weather data (water temp, air temp, conditions)