#!/bin/bash

# Pipecat Migration Setup Script

echo "🎙️ Voice Patient Registration - Pipecat Setup"
echo "=============================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from .env.example..."
    cp .env.example .env
    echo "✅ .env file created"
else
    echo "ℹ️  .env file already exists"
fi

echo ""
echo "🔑 API Keys Required:"
echo ""
echo "1. Google Gemini API Key"
echo "   Get it at: https://aistudio.google.com/apikey"
echo "   Free tier available"
echo ""
echo "2. Daily.co API Key"
echo "   Get it at: https://daily.co"
echo "   Free tier: 10,000 minutes/month"
echo ""
echo "3. Deepgram API Key"
echo "   Get it at: https://deepgram.com"
echo "   Free tier: 200 minutes/month"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo ""

# Ask if user wants to install dependencies
read -p "📦 Install Python dependencies now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
    echo "✅ Dependencies installed"
else
    echo "⏭️  Skipping dependency installation"
    echo "   Run 'pip install -r requirements.txt' manually later"
fi

echo ""
echo "📝 Next Steps:"
echo ""
echo "1. Edit .env file and add your API keys:"
echo "   - GEMINI_API_KEY"
echo "   - DAILY_API_KEY"
echo "   - DEEPGRAM_API_KEY"
echo ""
echo "2. Start the database:"
echo "   docker-compose up -d postgres"
echo ""
echo "3. Run database migrations:"
echo "   alembic upgrade head"
echo ""
echo "4. Start the server:"
echo "   uvicorn app.main:app --reload"
echo ""
echo "5. Test the voice interface:"
echo "   Open http://localhost:8000/ui/voice.html"
echo ""
echo "📚 For more details, see MIGRATION_GUIDE.md"
echo ""
echo "✨ Setup complete!"
