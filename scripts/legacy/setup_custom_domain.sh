#!/bin/bash
# Set up omega.local pointing to localhost for easier dashboard access

DOMAIN="omega.local"
IP="127.0.0.1"

echo "🔧 Setting up local domain: $DOMAIN"

if grep -q "$DOMAIN" /etc/hosts; then
    echo "✅ $DOMAIN is already enabled in /etc/hosts"
else
    echo "🔒 Requesting sudo to add '$DOMAIN' to /etc/hosts..."
    # Prepare the line to add
    ENTRY="$IP $DOMAIN"
    
    # Append safely
    if echo "$ENTRY" | sudo tee -a /etc/hosts > /dev/null; then
        echo "✅ Added: $ENTRY"
    else
        echo "❌ Failed to edit /etc/hosts. Please check permissions."
        exit 1
    fi
fi

echo ""
echo "🎉 Success! You can now access the dashboard at:"
echo "👉 http://$DOMAIN:8080"
echo ""
