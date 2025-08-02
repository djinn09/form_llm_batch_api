#!/bin/bash
set -e

echo "--- Starting Project Setup ---"

# --- Check for Python ---
if ! command -v python3 &> /dev/null
then
    echo "ERROR: python3 could not be found. Please install Python 3.9 or higher."
    exit 1
fi

echo "Python 3 found."

# --- Create Virtual Environment ---
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment '$VENV_DIR' already exists. Skipping creation."
else
    echo "Creating virtual environment in '$VENV_DIR'..."
    if command -v uv &> /dev/null; then
        uv venv
    else
        python3 -m venv $VENV_DIR
    fi
    echo "Virtual environment created."
fi

# --- Activate Virtual Environment and Install Dependencies ---
echo "Activating virtual environment and installing dependencies..."
source $VENV_DIR/bin/activate

if command -v uv &> /dev/null; then
    echo "Using uv to install dependencies from pyproject.toml..."
    uv pip install -e ".[dev]"
else
    echo "Using pip to install dependencies from pyproject.toml..."
    pip install -e ".[dev]"
fi
echo "Dependencies installed successfully."

# --- Set up pre-commit hooks ---
echo "Installing pre-commit hooks..."
pre-commit install
echo "Pre-commit hooks installed."

echo ""
echo "--- Project Setup Complete ---"
echo "The virtual environment is ready and dependencies are installed."
echo "To activate the virtual environment in your shell, run:"
echo "source .venv/bin/activate"
echo "--------------------------------"
