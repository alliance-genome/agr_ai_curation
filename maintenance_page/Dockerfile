# Simple nginx-based maintenance page
FROM nginx:alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/maintenance.conf

# Copy static files
COPY index.html /usr/share/nginx/html/
COPY maintenance_logo.png /usr/share/nginx/html/

# The maintenance_message.txt will be mounted as a volume at runtime

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
