# Maintenance Mode

This directory contains the maintenance page that is displayed when the AI Curation service is offline for scheduled maintenance.

## Components

1. **Maintenance Banner** (in main app) - Yellow banner at the top of the screen to notify users of upcoming maintenance
2. **Maintenance Page** (this directory) - Static page shown when the main services are down

## Quick Reference

### Show Advance Notice Banner

Edit `config/maintenance_message.txt`:

```
# Uncomment and edit the message below:
Scheduled maintenance: The AI Curation service will be unavailable on January 15, 2025 from 2:00 PM - 4:00 PM EST for system updates.
```

The banner will automatically appear on the next page load (or within 5 minutes for existing sessions).

To remove the banner, comment out or delete the message line.

### Switch to Maintenance Mode

When it's time to take the site down:

```bash
# 1. Update the maintenance message in maintenance_message.txt

# 2. Stop the main services
docker compose down

# 3. Start the maintenance page
docker compose -f docker-compose.maintenance.yml up -d
```

### Restore Normal Operation

```bash
# 1. Stop the maintenance page
docker compose -f docker-compose.maintenance.yml down

# 2. Start the main services
docker compose up -d

# 3. (Optional) Clear the maintenance message in config/maintenance_message.txt
```

## Files

- `index.html` - The maintenance page HTML/CSS/JS
- `nginx.conf` - Nginx configuration for serving the page
- `Dockerfile` - Container definition
- `maintenance_logo.png` - **YOU NEED TO ADD THIS** - The Alliance maintenance logo

## Logo Setup

You need to add the maintenance logo image:

1. Save your maintenance logo as `maintenance_page/maintenance_logo.png`
2. Recommended size: ~800-1200px wide for best display

The logo you showed with the robot and hexagonal organism icons should be saved here.

## How It Works

### Maintenance Banner (Advance Notice)

1. Backend endpoint `/api/maintenance/message` reads `config/maintenance_message.txt`
2. Frontend `MaintenanceBanner` component fetches this endpoint on page load
3. If a message exists (non-empty, non-comment line), the yellow banner appears
4. Re-checks every 5 minutes in case the message changes

### Maintenance Page (Site Down)

1. A separate nginx container serves the static maintenance page
2. Runs on the same port (3002) as the main frontend
3. Reads `config/maintenance_message.txt` via JavaScript to show the current message
4. Returns 503 for API requests (good for monitoring tools)

## Customization

### Banner Styling

Edit `frontend/src/components/MaintenanceBanner.tsx` to change:
- Colors (currently yellow/orange to match connection warnings)
- Position, padding, font size
- Icon

### Maintenance Page Styling

Edit `maintenance_page/index.html` to change:
- Colors, fonts, layout
- Additional information or links
- Contact email address
