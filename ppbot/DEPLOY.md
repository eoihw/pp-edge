# Put the web app online (free) and use it from any browser

You need: a GitHub account, a Streamlit Community Cloud account (sign in with GitHub at share.streamlit.io), and your Odds API key.

1. **GitHub:** create a new repository (set it to Private). Click "uploading an existing file" and drag in EVERYTHING from this folder
   (app.py, webcore.py, ppbot.py, ev.py, context.py, niche.py, requirements.txt, ...). Do NOT upload any file containing your API key. Commit.
2. **Streamlit Cloud:** New app -> pick your repo -> main file path `app.py` -> Advanced settings -> Secrets, paste:
   ```
   ODDS_API_KEY = "your_key_here"
   APP_PASSWORD = "pick_a_long_password"
   ```
   Deploy. After a minute you get a link. Open it on your phone or at school and enter the password.
3. **Use it:** pick sports and a day, press **Analyze**. "Scan games (free)" lists the games without spending credits.
   Identical runs within 15 minutes reuse the previous result, so re-pressing the button doesn't burn credits.

Run it on your own PC instead: `py -m pip install -r requirements.txt` then `py -m streamlit run app.py`.

Notes: your old API key was visible in screenshots; make a new one on the Odds API site and use that. Some school Wi-Fi blocks streamlit.app; your phone's data will work.
