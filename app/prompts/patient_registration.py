"""Patient registration prompt and tool definitions for Gemini."""

from google.genai import types

SYSTEM_PROMPT = """
You are Alex, a warm patient intake coordinator at a medical clinic.

## YOUR GOAL
Collect patient registration information through natural conversation.

## REQUIRED FIELDS (collect in order, but adapt naturally)
1. first_name, last_name (1-50 chars, alphabetic with hyphens/apostrophes)
2. date_of_birth (must be past date, MM/DD/YYYY)
3. sex (Male/Female/Other/Decline to Answer)
4. phone_number (10-digit U.S.)
5. address_line_1, city, state (2-letter abbreviation), zip_code (5-digit or ZIP+4)

## OPTIONAL FIELDS
After required fields, ask: "I can also collect your email, insurance information, \
an emergency contact, and language preference. Would you like to provide any of those?"

Optional fields: email, address_line_2, insurance_provider, insurance_member_id, \
emergency_contact_name, emergency_contact_phone, preferred_language

## BONUS: MULTI-LANGUAGE SUPPORT
If the caller speaks Spanish (e.g., "Hablo español"), immediately switch to speaking \
fluent Spanish for the remainder of the call. Record `preferred_language` as "Spanish".

## TOOLS AVAILABLE
- start_call(phone_number) → initialize call tracking, check for existing patient
- validate_field(field_name, field_value) → validate a single field in real-time before final submission
- check_duplicate(phone_number) → call this after collecting phone number
- update_field(field_name, field_value) → update a single field when caller makes a correction
- get_progress() → check which fields collected, which still needed
- confirm_ready() → validate all required fields collected before reading back
- confirm_completed() → mark that caller confirmed all information is correct
- save_patient(data) → call ONLY after confirm_completed() succeeds
- update_patient(patient_id, data) → call if duplicate detected and caller wants to update
- reset_registration() → clear all data and start over when caller says "start over"
- schedule_appointment(patient_id, preferred_day, preferred_time) → call AFTER saving/updating
- save_turn(speaker, message) → save conversation for transcript (call periodically)
- end_call(outcome, summary) → mark call complete and save transcript

## CONVERSATION RULES
- ALWAYS call start_call() at the beginning with phone number once collected
- One or two fields per turn maximum — don't overwhelm the caller
- Use validate_field to check data in real-time (e.g., after collecting phone number)
- If validation fails: explain the issue naturally and re-ask for that specific field
- Handle corrections gracefully: "Actually it's D-A-V-I-S" → use update_field tool, say "Got it, I've updated that."
- Use get_progress() periodically to check what fields are still needed
- Before reading back: call confirm_ready() to ensure all required fields collected
- Read ALL collected fields back clearly and ask "Does everything look correct?"
- After caller confirms: call confirm_completed() to mark confirmation done
- ONLY call save_patient() AFTER confirm_completed() succeeds
- If caller says "start over" or "restart": use reset_registration tool, acknowledge warmly
- If check_duplicate finds a match: "It looks like we already have a record for [name]. \
  Would you like to update your information instead?"
- After save_patient or update_patient succeeds: "You're all set! We have availability \
  for a first appointment next week. Would you like me to schedule that now?" \
  (If yes, use the schedule_appointment tool)
- Call save_turn(speaker, message) periodically to build transcript
- At call end: call end_call(outcome, summary) to save transcript and complete tracking
- If a tool returns an error: explain the issue naturally and re-ask for the corrected info

## TONE
Warm, concise, professional. Never robotic. Never read out validation rules or error codes.
Use natural language confirmations like "Got it!" or "Perfect, thanks!"
"""

# Tool definitions for Gemini function calling
TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="start_call",
            description="Initialize call tracking and check for existing patient. Call this at the beginning after collecting phone number.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "phone_number": types.Schema(
                        type=types.Type.STRING,
                        description="The caller's phone number (10 digits)",
                    ),
                },
                required=["phone_number"],
            ),
        ),
        types.FunctionDeclaration(
            name="validate_field",
            description="Validate a single field in real-time before final submission. Use this to catch errors early and provide immediate feedback.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "field_name": types.Schema(
                        type=types.Type.STRING,
                        description="Name of the field to validate (e.g., 'phone_number', 'date_of_birth', 'email', 'state', 'zip_code')",
                    ),
                    "field_value": types.Schema(
                        type=types.Type.STRING,
                        description="The value to validate",
                    ),
                },
                required=["field_name", "field_value"],
            ),
        ),
        types.FunctionDeclaration(
            name="check_duplicate",
            description="Check if a patient with this phone number already exists in the system. Call this after collecting the phone number.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "phone_number": types.Schema(
                        type=types.Type.STRING,
                        description="The patient's 10-digit US phone number (digits only)",
                    ),
                },
                required=["phone_number"],
            ),
        ),
        types.FunctionDeclaration(
            name="update_field",
            description="Update a single field in the current registration draft when the caller makes a correction. Use this for mid-conversation corrections.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "field_name": types.Schema(
                        type=types.Type.STRING,
                        description="Name of the field to update (e.g., 'last_name', 'city', 'email')",
                    ),
                    "field_value": types.Schema(
                        type=types.Type.STRING,
                        description="The corrected value",
                    ),
                },
                required=["field_name", "field_value"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_progress",
            description="Check which fields have been collected and which are still needed. Helps track registration progress.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="confirm_ready",
            description="Validate that all required fields have been collected before reading back to the caller. Returns list of missing fields if any.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="confirm_completed",
            description="Mark that the caller has confirmed all information is correct. MUST be called before save_patient.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="save_patient",
            description="Save a new patient registration to the database. Call ONLY after confirm_completed() succeeds.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "first_name": types.Schema(type=types.Type.STRING, description="Patient's first name (1-50 chars, alphabetic)"),
                    "last_name": types.Schema(type=types.Type.STRING, description="Patient's last name (1-50 chars, alphabetic)"),
                    "date_of_birth": types.Schema(type=types.Type.STRING, description="Date of birth in MM/DD/YYYY format"),
                    "sex": types.Schema(type=types.Type.STRING, description="Male, Female, Other, or Decline to Answer"),
                    "phone_number": types.Schema(type=types.Type.STRING, description="10-digit US phone number (digits only)"),
                    "email": types.Schema(type=types.Type.STRING, description="Email address (optional)"),
                    "address_line_1": types.Schema(type=types.Type.STRING, description="Street address"),
                    "address_line_2": types.Schema(type=types.Type.STRING, description="Apt/Suite/Unit (optional)"),
                    "city": types.Schema(type=types.Type.STRING, description="City name (1-100 chars)"),
                    "state": types.Schema(type=types.Type.STRING, description="2-letter US state abbreviation"),
                    "zip_code": types.Schema(type=types.Type.STRING, description="5-digit or ZIP+4 code"),
                    "insurance_provider": types.Schema(type=types.Type.STRING, description="Insurance company name (optional)"),
                    "insurance_member_id": types.Schema(type=types.Type.STRING, description="Insurance member/subscriber ID (optional)"),
                    "emergency_contact_name": types.Schema(type=types.Type.STRING, description="Emergency contact full name (optional)"),
                    "emergency_contact_phone": types.Schema(type=types.Type.STRING, description="Emergency contact 10-digit phone (optional)"),
                    "preferred_language": types.Schema(type=types.Type.STRING, description="Preferred language (optional, default English)"),
                },
                required=["first_name", "last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code"],
            ),
        ),
        types.FunctionDeclaration(
            name="update_patient",
            description="Update an existing patient's information. Use when a duplicate phone number is found and the caller wants to update their record.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "patient_id": types.Schema(type=types.Type.STRING, description="UUID of the existing patient to update"),
                    "first_name": types.Schema(type=types.Type.STRING, description="Updated first name"),
                    "last_name": types.Schema(type=types.Type.STRING, description="Updated last name"),
                    "date_of_birth": types.Schema(type=types.Type.STRING, description="Updated date of birth in MM/DD/YYYY"),
                    "sex": types.Schema(type=types.Type.STRING, description="Updated sex"),
                    "phone_number": types.Schema(type=types.Type.STRING, description="Updated phone number"),
                    "email": types.Schema(type=types.Type.STRING, description="Updated email"),
                    "address_line_1": types.Schema(type=types.Type.STRING, description="Updated street address"),
                    "address_line_2": types.Schema(type=types.Type.STRING, description="Updated apt/suite/unit"),
                    "city": types.Schema(type=types.Type.STRING, description="Updated city"),
                    "state": types.Schema(type=types.Type.STRING, description="Updated state abbreviation"),
                    "zip_code": types.Schema(type=types.Type.STRING, description="Updated zip code"),
                    "insurance_provider": types.Schema(type=types.Type.STRING, description="Updated insurance provider"),
                    "insurance_member_id": types.Schema(type=types.Type.STRING, description="Updated insurance member ID"),
                    "emergency_contact_name": types.Schema(type=types.Type.STRING, description="Updated emergency contact name"),
                    "emergency_contact_phone": types.Schema(type=types.Type.STRING, description="Updated emergency contact phone"),
                    "preferred_language": types.Schema(type=types.Type.STRING, description="Updated preferred language"),
                },
                required=["patient_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="reset_registration",
            description="Clear all collected data and start the registration process over from the beginning. Use when the caller says 'start over', 'restart', or 'begin again'.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="save_turn",
            description="Save a conversation turn for transcript building. Call periodically to capture the conversation.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "speaker": types.Schema(
                        type=types.Type.STRING,
                        description="Who is speaking: 'user' or 'agent'",
                    ),
                    "message": types.Schema(
                        type=types.Type.STRING,
                        description="The message content",
                    ),
                },
                required=["speaker", "message"],
            ),
        ),
        types.FunctionDeclaration(
            name="end_call",
            description="Mark the call as complete and save the transcript. Call at the end of the conversation.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "outcome": types.Schema(
                        type=types.Type.STRING,
                        description="Call outcome: 'completed', 'abandoned', 'failed', or 'error'",
                    ),
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description="Brief summary of the call",
                    ),
                },
                required=["outcome"],
            ),
        ),
        types.FunctionDeclaration(
            name="schedule_appointment",
            description="Schedule an initial appointment for a registered patient. Offers available time slots.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "patient_id": types.Schema(type=types.Type.STRING, description="UUID of the patient"),
                    "preferred_day": types.Schema(
                        type=types.Type.STRING,
                        description="Preferred day of week (optional, e.g., 'Monday', 'Tuesday')",
                    ),
                    "preferred_time": types.Schema(
                        type=types.Type.STRING,
                        description="Preferred time of day (optional, e.g., 'morning', 'afternoon', 'evening')",
                    ),
                },
                required=["patient_id"],
            ),
        ),
    ]),
]
