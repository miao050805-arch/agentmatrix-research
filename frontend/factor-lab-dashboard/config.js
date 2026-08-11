// GitHub Pages can only host the static dashboard. Set this to a deployed
// Flask base URL, for example "https://factor-lab-api.onrender.com".
// Port contract: local development reserves 8012 for the Flask API
// (backend/factor_lab_api.py); the cloud server (Render) injects $PORT at
// runtime, so no port is hard-coded here. You can also point a running
// browser session at any API host with ?api=https://<host>.
window.FACTOR_LAB_API_HOST = "";

// Supabase public read endpoint for the static dashboard. This key is safe to
// expose only when Row Level Security allows SELECT on public dashboard tables.
window.FACTOR_LAB_SUPABASE_URL = "https://rebyrzrvnfbwvmbjvhzj.supabase.co";
window.FACTOR_LAB_SUPABASE_ANON_KEY = "sb_publishable_ZHAM5wQWZh_Wng4TaL-fDg_XlFBcB6j";
window.FACTOR_LAB_SUPABASE_FACTOR_TABLE = "public_dashboard_factors";
window.FACTOR_LAB_SUPABASE_TRUTH_SUMMARY_TABLE = "factor_truth_values_summary";
window.FACTOR_LAB_SUPABASE_ANALYSIS_TABLE = "factor_analysis_results";
window.FACTOR_LAB_SUPABASE_ANALYSIS_IC_TABLE = "factor_analysis_ic_series";
window.FACTOR_LAB_SUPABASE_ANALYSIS_GROUP_TABLE = "factor_analysis_group_series";

// Frontend-only access gate for the static dashboard. This is a convenience
// login screen, not a substitute for Supabase RLS or backend authentication.
window.FACTOR_LAB_ACCESS_PASSWORD = "factorlab2026";
