from google import genai

with open("geminikey.txt") as f:
    gemini_client = genai.Client(api_key=f.read())

for i in range(50):
    try:
        resp = gemini_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=genai.types.Part.from_text(text="say one word."),
            config=genai.types.GenerateContentConfig(
                temperature=2,
                thinking_config=genai.types.ThinkingConfig(thinking_budget=0)
            ),
        )
        print(resp.text)
    except Exception as e:
        print(e)
        print("waiting 2 seconds")
