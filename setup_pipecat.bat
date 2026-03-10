@echo off
REM Pipecat Migration Setup Script for Windows

echo.
echo 🎙️ Voice Patient Registration - Pipecat Setup
echo ==============================================
echo.

REM Check if .env exists
if not exist .env (
    echo 📝 Creating .env file from .env.example...
    copy .env.example .env
    echo ✅ .env file created
) else (
    echo ℹ️  .env file already exists
)

echo.
echo 🔑 API Keys Required:
echo.
echo 1. Google Gemini API Key
echo    Get it at: https://aistudio.google.com/apikey
echo    Free tier available
echo.
echo 2. Daily.co API Key
echo    Get it at: https://daily.co
echo    Free tier: 10,000 minutes/month
echo.
echo 3. Deepgram API Key
echo    Get it at: https://deepgram.com
echo    Free tier: 200 minutes/month
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 3 is not installed. Please install Python 3.8 or higher.
    pause
    exit /b 1
)

echo ✅ Python 3 found
python --version
echo.

REM Ask if user wants to install dependencies
set /p INSTALL="📦 Install Python dependencies now? (y/n) "
if /i "%INSTALL%"=="y" (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo ✅ Dependencies installed
) else (
    echo ⏭️  Skipping dependency installation
    echo    Run 'pip install -r requirements.txt' manually later
)

echo.
echo 📝 Next Steps:
echo.
echo 1. Edit .env file and add your API keys:
echo    - GEMINI_API_KEY
echo    - DAILY_API_KEY
echo    - DEEPGRAM_API_KEY
echo.
echo 2. Start the database:
echo    docker-compose up -d postgres
echo.
echo 3. Run database migrations:
echo    alembic upgrade head
echo.
echo 4. Start the server:
echo    uvicorn app.main:app --reload
echo.
echo 5. Test the voice interface:
echo    Open http://localhost:8000/ui/voice.html
echo.
echo 📚 For more details, see MIGRATION_GUIDE.md
echo.
echo ✨ Setup complete!
echo.
pause
