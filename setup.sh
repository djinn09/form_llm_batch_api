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

# --- Check for uv and install if missing ---
if ! command -v uv &> /dev/null; then
    echo "'uv' not found. Attempting to install it..."

    if command -v curl &> /dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget &> /dev/null; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "ERROR: Neither 'curl' nor 'wget' is available to download 'uv'."
        exit 1
    fi

    # Ensure ~/.local/bin is in PATH for this script
    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv &> /dev/null; then
        echo "ERROR: 'uv' installation failed or is not in PATH. Please ensure '~/.local/bin' is in your PATH."
        exit 1
    fi

    echo "'uv' installed successfully."
else
    echo "'uv' is already installed."
fi

# --- Create Virtual Environment with uv ---
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment '$VENV_DIR' already exists. Skipping creation."
else
    echo "Creating virtual environment in '$VENV_DIR' using uv..."
    uv venv
    echo "Virtual environment created."
fi

# --- Activate Virtual Environment and Install Dependencies ---
echo "Activating virtual environment and installing dependencies..."
source $VENV_DIR/bin/activate

echo "Installing dependencies from pyproject.toml using uv..."
uv pip install -e ".[dev]"
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
