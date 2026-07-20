# Upstream API Emulator

A simple proxy server that time-shifts requests to the real upstream API, allowing you to test with past or future dates using real performance data.

## How It Works

1. Receives a request with a `dateFrom` parameter
2. Shifts the date forward/backward by the configured number of days
3. Fetches real data from the upstream API using the shifted date
4. Shifts all dates in the response back to match the original request
5. Returns the modified response

## Installation

No additional dependencies needed - uses the same requirements as the main API.

## Usage

### Basic Usage

```bash
# Shift dates 10 days into the future
python emulate_upstream.py --timeshift-days 10

# Shift dates 5 days into the past
python emulate_upstream.py --timeshift-days -5

# Run on a custom port
python emulate_upstream.py --timeshift-days 10 --port 5001
```

### Test the Emulator

```bash
# Health check
curl "http://localhost:5000/health"

# Get calendar data
curl "http://localhost:5000/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-18&numberOfDays=1"
```

### Using with Your API

To use the emulator with your main API, update the upstream URL in `src/core/wave_calendar.py`:

```python
# Change this line:
url = "https://ticketing-api.thewave.com/api/twb-prod/b2c/v1/events/calendar"

# To this (assuming emulator is on port 5000):
url = "http://localhost:5000/api/twb-prod/b2c/v1/events/calendar"
```

Or use an environment variable:

```bash
export UPSTREAM_API_URL="http://localhost:5000/api/twb-prod/b2c/v1/events/calendar"
```

## Examples

### Example 1: Test Future Dates

Want to see what performances are available 10 days from now using today's data?

```bash
# Start emulator with 10-day shift
python emulate_upstream.py --timeshift-days 10

# Request for "future" date
curl "http://localhost:5000/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-28&numberOfDays=1"

# This actually fetches data from 2026-02-07 (28 + 10 days)
# But returns it with dates shifted back to 2026-01-28
```

### Example 2: Test Historical Predictions

Want to test your temperature prediction accuracy?

```bash
# Shift 7 days back to get "past" data
python emulate_upstream.py --timeshift-days -7

# Request shows data from 7 days ago as if it were today
curl "http://localhost:5000/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-18&numberOfDays=1"
```

### Example 3: Full Integration Test

Terminal 1 - Start the emulator:
```bash
python emulate_upstream.py --timeshift-days 10 --port 5001
```

Terminal 2 - Update your API to use the emulator:
```bash
# Temporarily modify wave_calendar.py to point to localhost:5001
# Then start your API
python main.py
```

Terminal 3 - Test your API:
```bash
curl "http://localhost:5000/calendar?dateFrom=2026-02-01&numberOfDays=7"
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--timeshift-days` | Days to shift (positive=future, negative=past) | 0 |
| `--port` | Port to run on | 5000 |
| `--host` | Host to bind to | 0.0.0.0 |
| `--debug` | Enable Flask debug mode | False |

## How Dates Are Shifted

### Request Flow

1. **Incoming request**: `dateFrom=2026-01-18` with `timeshift-days=10`
2. **Shifted upstream request**: Fetches data for `2026-01-28`
3. **Response transformation**: All `date` fields in response are shifted back by 10 days
4. **Returned response**: Shows data with `date=2026-01-18`

### What Gets Shifted

The emulator shifts:
- Query parameter: `dateFrom`
- Response fields: All `"date"` keys in the JSON (recursively)

Example response transformation:
```json
// Upstream returns (for 2026-01-28):
{
  "days": [{
    "date": "2026-01-28",
    "performances": [{
      "date": "2026-01-28",
      "time": "10:00:00.000"
    }]
  }]
}

// Emulator returns (shifted back):
{
  "days": [{
    "date": "2026-01-18",
    "performances": [{
      "date": "2026-01-18",
      "time": "10:00:00.000"
    }]
  }]
}
```

## Limitations

- Only shifts the `dateFrom` parameter and `date` fields
- Does not modify `time` or `timeEnd` fields
- Performance availability numbers are from the shifted date (not recalculated)
- Cannot shift beyond the upstream API's available date range

## Use Cases

1. **Testing future features**: Test predicted temperature features without waiting for future dates
2. **Demo purposes**: Show "future" bookings using real historical data
3. **Development**: Test date-dependent logic with consistent data
4. **QA**: Verify temperature merging logic works correctly for past/current/future performances

## Troubleshooting

**Emulator returns 502 errors**
- Check that you have internet connectivity
- Verify the upstream API is accessible: `curl https://ticketing-api.thewave.com`

**Dates in response don't match request**
- Check the `timeshift-days` value - it should shift dates correctly
- Verify you're looking at the `date` fields (not `dateFrom` query param)

**No data returned**
- The shifted date might be outside the upstream API's available range
- Try a smaller timeshift value or different date range
