#!/bin/bash
set -e

# Define build directory
BUILD_DIR="temp_cloud_build"

echo "🧹 Cleaning build directory: $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "📦 Copying files..."
cp omega_cloud_worker.py "$BUILD_DIR/"
cp config.py "$BUILD_DIR/"
cp profiles.py "$BUILD_DIR/"
cp gcp_auth.py "$BUILD_DIR/"
cp gcs_jobs.py "$BUILD_DIR/"
cp subtitle_standards.py "$BUILD_DIR/"
cp Dockerfile "$BUILD_DIR/"
# Copy requirements but place in cloud/ for consistency with Dockerfile expectation
mkdir -p "$BUILD_DIR/cloud"
cp cloud/requirements.txt "$BUILD_DIR/cloud/"
# Copy providers
cp -r providers "$BUILD_DIR/"

echo "🚀 Submitting build from $BUILD_DIR..."
cd "$BUILD_DIR"

# Verify size
DU_SIZE=$(du -sh . | awk '{print $1}')
echo "   Build context size: $DU_SIZE"

# Submit build
gcloud builds submit --tag gcr.io/sermon-translator-system/omega-cloud-worker:latest --project=sermon-translator-system --quiet

echo "✅ Build submitted."
cd ..
rm -rf "$BUILD_DIR"
