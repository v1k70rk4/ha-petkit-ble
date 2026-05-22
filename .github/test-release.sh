#!/bin/bash

# Test script for automated release workflows
# Usage: ./test-release.sh [version]

set -e

VERSION=${1:-$(date +%Y%m%d%H%M%S)}
TAG="v0.1.${VERSION}"

echo "🚀 Testing automated release workflows with tag: $TAG"
echo ""

# Create and push tag
echo "📝 Creating tag..."
git tag -a "$TAG" -m "Test automated release workflows - $TAG"

echo "🔄 Pushing tag to trigger workflows..."
git push origin "$TAG"

echo ""
echo "✅ Tag $TAG pushed successfully!"
echo ""
echo "🔍 Monitor workflows at:"
echo "   https://github.com/v1k70rk4/ha-petkit-ble/actions"
echo ""
echo "📦 View releases at:"
echo "   https://github.com/v1k70rk4/ha-petkit-ble/releases"
echo ""
echo "⏱️  Workflows should complete within 2-3 minutes"
echo "🎯 Expected: 4 workflows will run, at least one will create/update the release"