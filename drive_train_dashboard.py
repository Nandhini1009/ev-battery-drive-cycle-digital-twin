import streamlit as st
import pandas as pd
import os
import time
import atexit
import matplotlib.pyplot as plt

from gpiozero import Device, PWMOutputDevice
from gpiozero.pins.lgpio import LGPIOFactory

# Set pin factory for Raspberry Pi 5 compatibility
Device.pin_factory = LGPIOFactory()

# === PAGE CONFIGURATION ===
st.set_page_config(
    page_title="Drive Train",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === CUSTOM CSS ===
st.markdown("""
<style>
    .main-header {
        text-align: center;
        background: linear-gradient(90deg, #43cea2, #185a9d);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 3rem;
        font-weight: bold;
        margin-bottom: 2rem;
    }
   
    .config-box {
        background-color: #f0f2f6;
        padding: 1.5rem;
        border-radius: 10px;
        border-left: 5px solid #43cea2;
        margin: 1rem 0;
    }
   
    .status-container {
        background: linear-gradient(135deg, #11998e, #38ef7d);
        color: white;
        padding: 1.5rem;
        border-radius: 15px;
        text-align: center;
        margin: 1rem 0;
    }
   
    .metric-card {
        background: white;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        text-align: center;
        border: 1px solid #e0e0e0;
    }
   
    .stButton > button[kind="primary"] {
        background: linear-gradient(45deg, #28a745, #218838) !important;
        color: white !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        border-radius: 25px !important;
        font-weight: bold !important;
        font-size: 1.1rem !important;
        cursor: pointer !important;
    }
   
    .stButton > button[kind="secondary"] {
        background: linear-gradient(45deg, #dc3545, #c82333) !important;
        color: white !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        border-radius: 25px !important;
        font-weight: bold !important;
        font-size: 1.1rem !important;
        cursor: pointer !important;
    }
   
    .stSelectbox > div > div {
        background-color: #f8f9fa;
        border-radius: 10px;
    }
</style>
""", unsafe_allow_html=True)

# === CONFIGURATION ===
PWM_PIN = 18
FREQ = 1000
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "duty_cycles")

# Initialize session state variables
def initialize_session_state():
    """Initialize all session state variables to avoid errors"""
    if "pwm_initialized" not in st.session_state:
        try:
            st.session_state.pwm = PWMOutputDevice(PWM_PIN, frequency=FREQ, initial_value=0)
            st.session_state.pwm_status = "✅ Connected"
            st.session_state.pwm_initialized = True
        except Exception as e:
            st.error(f"❌ Failed to initialize PWM on pin {PWM_PIN}: {e}")
            st.session_state.pwm_status = "❌ Error"
            st.stop()
   
    # Control state variables (separate from widget keys to avoid session state errors)
    if "is_running" not in st.session_state:
        st.session_state.is_running = False
    if "is_paused" not in st.session_state:
        st.session_state.is_paused = False
    if "stop_requested" not in st.session_state:
        st.session_state.stop_requested = False
    if "current_step" not in st.session_state:
        st.session_state.current_step = 0
    if "start_time" not in st.session_state:
        st.session_state.start_time = 0
    if "total_duration" not in st.session_state:
        st.session_state.total_duration = 0
    if "cycle_data" not in st.session_state:
        st.session_state.cycle_data = None

initialize_session_state()
pwm = st.session_state.pwm

# Cleanup function
def cleanup_pwm():
    """Clean up PWM resources on exit"""
    if hasattr(st.session_state, 'pwm') and st.session_state.pwm:
        st.session_state.pwm.off()

atexit.register(cleanup_pwm)

def list_drive_cycles(folder):
    """List all Excel files in the specified folder"""
    try:
        return [f for f in os.listdir(folder) if f.endswith(".xlsx")]
    except FileNotFoundError:
        return []

def load_cycle(path):
    """Read a drive cycle Excel file and return a table with 'time' and 'DutyCycle' columns.

    The duty cycle column header differs between files (DUTY CYCLE, duty cycle,
    DUTYCYCLE, DutyCycle), so it is found by name, ignoring case and spaces.
    """
    df = pd.read_excel(path)
    for col in df.columns:
        if str(col).replace(" ", "").lower() == "dutycycle":
            df = df.rename(columns={col: "DutyCycle"})
            break
    if "time" in df.columns and "DutyCycle" in df.columns:
        df = df[["time", "DutyCycle"]]
    return df

# Callback functions for buttons (prevents session state errors)
def handle_start_pause():
    """Callback for start/pause button - toggles between start, pause, and resume"""
    if not st.session_state.is_running:
        # Start the process
        st.session_state.is_running = True
        st.session_state.is_paused = False
        st.session_state.stop_requested = False
        st.session_state.current_step = 0
        st.session_state.start_time = time.time()
    else:
        # Toggle pause/resume
        st.session_state.is_paused = not st.session_state.is_paused

def handle_stop():
    """Callback for stop button - completely stops the process"""
    st.session_state.stop_requested = True
    st.session_state.is_running = False
    st.session_state.is_paused = False
    st.session_state.current_step = 0
    if hasattr(st.session_state, 'pwm') and st.session_state.pwm:
        st.session_state.pwm.off()

def run_drive_cycle(excel_file):
    """Run the drive cycle with real-time updates and live graph"""
    try:
        df = load_cycle(excel_file)
       
        if "time" not in df.columns or "DutyCycle" not in df.columns:
            st.error("❌ Excel file must contain 'time' and 'DutyCycle' columns")
            return
       
        times = df["time"].tolist()
        duties = df["DutyCycle"].tolist()
       
        if len(times) == 0 or len(duties) == 0:
            st.error("❌ No data found in the Excel file")
            return

        # Normalize duty cycles to 0-1 range
        max_duty = max(duties) if duties and max(duties) > 0 else 1
        duties = [max(0, min(1, d / max_duty)) for d in duties]
       
        total_steps = len(times)
        st.session_state.total_duration = times[-1]
        st.session_state.cycle_data = {"times": times, "duties": duties}
       
        # Create containers for real-time updates
        status_container = st.container()
        with status_container:
            if st.session_state.is_running and not st.session_state.is_paused:
                status_text = "🔄 Running Drive Cycle"
            elif st.session_state.is_paused:
                status_text = "⏸ Drive Cycle Paused"
            else:
                status_text = "⏹ Drive Cycle Stopped"
               
            st.markdown(f"""
            <div class="status-container">
                <h3>{status_text}</h3>
                <p style="font-size: 1.2rem; margin: 0;">{os.path.basename(excel_file)}</p>
            </div>
            """, unsafe_allow_html=True)

        # Create metric columns - Four columns for the requested metrics
        col1, col2, col3, col4 = st.columns(4)
       
        # Create placeholders for live updates
        with col1:
            progress_placeholder = st.empty()
        with col2:
            duty_placeholder = st.empty()
        with col3:
            step_placeholder = st.empty()
        with col4:
            countdown_placeholder = st.empty()
       
        # Progress bar and graph placeholders
        progress_bar = st.progress(0)
        graph_placeholder = st.empty()

        # Main execution loop
        while (st.session_state.is_running and
               st.session_state.current_step < total_steps and
               not st.session_state.stop_requested):
           
            # Handle pause state
            if st.session_state.is_paused:
                time.sleep(0.1)
                continue
               
            i = st.session_state.current_step
           
            # Set PWM output
            pwm.value = duties[i]
           
            # Calculate time remaining
            elapsed = time.time() - st.session_state.start_time
            seconds_left = max(0, st.session_state.total_duration - elapsed)
           
            # Update metric cards with live data
            with progress_placeholder:
                progress_pct = (i + 1) / total_steps * 100
                st.markdown(f"""
                <div class="metric-card">
                    <h4 style="color:#43cea2;margin:0;">📊 Progress</h4>
                    <h2 style="margin:0.5rem 0;">{progress_pct:.1f}%</h2>
                </div>
                """, unsafe_allow_html=True)
           
            with duty_placeholder:
                st.markdown(f"""
                <div class="metric-card">
                    <h4 style="color:#ff6b6b;margin:0;">⚡ Duty Cycle</h4>
                    <h2 style="margin:0.5rem 0;">{duties[i]*100:.1f}%</h2>
                </div>
                """, unsafe_allow_html=True)
           
            with step_placeholder:
                st.markdown(f"""
                <div class="metric-card">
                    <h4 style="color:#667eea;margin:0;">🔢 Step</h4>
                    <h2 style="margin:0.5rem 0;">{i+1}/{total_steps}</h2>
                </div>
                """, unsafe_allow_html=True)
           
            with countdown_placeholder:
                st.markdown(f"""
                <div class="metric-card">
                    <h4 style="color:#28a745;margin:0;">⏱ Time Left</h4>
                    <h2 style="margin:0.5rem 0;">{seconds_left:.1f} s</h2>
                </div>
                """, unsafe_allow_html=True)
           
            # Update progress bar
            progress_bar.progress((i + 1) / total_steps)
           
            # Live graph update - shows current progress with countdown integration
            with graph_placeholder:
                fig, ax = plt.subplots(figsize=(12, 6))
               
                # Plot completed portion in green
                current_times = times[:i+1]
                current_duties = [d * 100 for d in duties[:i+1]]
                ax.plot(current_times, current_duties, color="#43cea2", linewidth=3, label="Completed", marker='o', markersize=4)
               
                # Plot remaining portion in light green
                if i < total_steps - 1:
                    remaining_times = times[i:]
                    remaining_duties = [d * 100 for d in duties[i:]]
                    ax.plot(remaining_times, remaining_duties, color="#b8e6d3", linewidth=2, alpha=0.6, label="Remaining", linestyle='--')
               
                # Mark current position with red dot
                if i < len(duties):
                    ax.scatter([times[i]], [duties[i] * 100], color="#ff6b6b", s=150, zorder=5, label="Current Position", edgecolor='white', linewidth=2)
               
                # Add vertical line showing time progress
                ax.axvline(x=times[i], color='orange', linestyle=':', alpha=0.7, label=f'Current Time: {times[i]:.1f}s')
               
                # Styling
                ax.set_xlabel("Time (s)", fontsize=12, fontweight='bold')
                ax.set_ylabel("Duty Cycle (%)", fontsize=12, fontweight='bold')
                ax.set_title(f"Live Drive Cycle Progress - {seconds_left:.1f}s Remaining", fontsize=14, fontweight='bold')
                ax.grid(True, alpha=0.3)
                ax.legend(loc='upper right')
                ax.set_ylim(0, max([d * 100 for d in duties]) * 1.1)
                ax.set_xlim(0, max(times) * 1.05)
               
                # Add background color
                fig.patch.set_facecolor('#f8f9fa')
                ax.set_facecolor('#ffffff')
               
                st.pyplot(fig)
                plt.close(fig)  # Important: close figure to prevent memory leaks
           
            # Calculate delay for next step
            if i < total_steps - 1:
                delay = max(0, times[i + 1] - times[i])
                time.sleep(delay)
           
            # Move to next step
            st.session_state.current_step += 1

        # Cleanup after completion or stop
        pwm.off()
       
        if st.session_state.current_step >= total_steps and not st.session_state.stop_requested:
            # Completed successfully
            st.markdown("""
            <div class="status-container">
                <h3>✅ Drive Cycle Completed Successfully!</h3>
                <p style="font-size: 1.1rem; margin: 0;">PWM output has been turned off</p>
            </div>
            """, unsafe_allow_html=True)
            st.balloons()  # Celebration animation
        elif st.session_state.stop_requested:
            st.error("⛔ Drive cycle stopped by user")
       
        # Reset states
        st.session_state.is_running = False
        st.session_state.is_paused = False
        st.session_state.current_step = 0
       
    except Exception as e:
        st.error(f"❌ Error running drive cycle: {e}")
        st.session_state.is_running = False
        st.session_state.is_paused = False

# === MAIN UI ===
st.markdown('<h1 class="main-header">🚗 Drive Train</h1>', unsafe_allow_html=True)

# Sidebar with system info and controls
with st.sidebar:
    st.markdown("### 🔧 System Configuration")
   
    st.markdown(f"""
    <div class="config-box">
        <strong>📍 PWM Pin:</strong> GPIO {PWM_PIN}<br>
        <strong>🔄 Frequency:</strong> {FREQ} Hz<br>
        <strong>📡 Status:</strong> {st.session_state.get('pwm_status','Unknown')}<br>
        <strong>📂 Directory:</strong> {os.path.basename(SCRIPT_DIR)}
    </div>
    """, unsafe_allow_html=True)
   
    # Additional controls in sidebar
    st.markdown("### 🛠 Additional Controls")
   
    if st.button("🔄 Reset PWM", use_container_width=True):
        if hasattr(st.session_state, 'pwm'):
            st.session_state.pwm.off()
        st.success("✅ PWM reset successfully!")
   
    if st.button("🔓 Release GPIO", use_container_width=True):
        if hasattr(st.session_state, 'pwm'):
            st.session_state.pwm.close()
            del st.session_state.pwm
            st.session_state.pwm_initialized = False
        st.success("✅ GPIO pin released!")
        st.rerun()

# Main content layout
col_left, col_right = st.columns([2, 1])

with col_left:
    st.markdown("### 📁 Select Drive Cycle File")
   
    files = list_drive_cycles(SCRIPT_DIR)
    if not files:
        st.markdown("""
        <div style="text-align: center; padding: 2rem; background-color: #fff3cd; border-radius: 10px; border-left: 5px solid #ffc107;">
            <h3>📂 No Excel Files Found</h3>
            <p>Please add .xlsx files containing drive cycle data to the duty_cycles folder.</p>
            <p><strong>Required columns:</strong> 'time' and 'DutyCycle'</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        selected_file = st.selectbox(
            "Choose a drive cycle file:",
            files,
            help="Select an Excel file containing 'time' and 'DutyCycle' columns"
        )
       
        # Control Panel with Green Start/Pause and Red Stop buttons
        st.markdown("### 🎛 Control Panel")
        c1, c2 = st.columns([1, 1])
       
        with c1:
            # Dynamic button text based on current state - Green button functionality
            if not st.session_state.is_running:
                button_text = "▶️ Start Drive Cycle"
                button_type = "primary"
            elif st.session_state.is_paused:
                button_text = "▶️ Resume"
                button_type = "primary"
            else:
                button_text = "⏸ Pause"
                button_type = "primary"
           
            st.button(button_text,
                     key="start_pause_button",
                     on_click=handle_start_pause,
                     type=button_type,
                     use_container_width=True)
       
        with c2:
            # Red stop button
            st.button("⏹ Stop",
                     key="stop_button",
                     on_click=handle_stop,
                     type="secondary",
                     use_container_width=True)
       
        # Current status display with color indicators
        if st.session_state.is_running or st.session_state.is_paused:
            if st.session_state.is_paused:
                st.markdown("🟡 **Status:** Drive cycle is paused - Click Resume to continue")
            else:
                st.markdown("🟢 **Status:** Drive cycle is running - Click Pause to pause")
        elif st.session_state.stop_requested:
            st.markdown("🔴 **Status:** Drive cycle stopped - Click Start to begin new cycle")
        else:
            st.markdown("⚪ **Status:** Ready to start - Select file and click Start")
       
        # Run drive cycle if conditions are met
        if (st.session_state.is_running and
            not st.session_state.stop_requested and
            'selected_file' in locals()):
            run_drive_cycle(os.path.join(SCRIPT_DIR, selected_file))

with col_right:
    # Replaced Excel sheet preview with matplotlib graph preview
    st.markdown("### 📊 Drive Cycle Preview")
   
    if files and 'selected_file' in locals():
        try:
            preview_df = load_cycle(os.path.join(SCRIPT_DIR, selected_file))
           
            # Enhanced file information box with more details
            st.markdown(f"""
            <div class="config-box">
                <strong>📄 File:</strong> {selected_file}<br>
                <strong>📈 Data Points:</strong> {len(preview_df)}<br>
                <strong>⏱ Total Duration:</strong> {preview_df['time'].max():.1f} seconds<br>
                <strong>🔋 Max Duty Cycle:</strong> {preview_df['DutyCycle'].max():.1f}%<br>
                <strong>🔋 Min Duty Cycle:</strong> {preview_df['DutyCycle'].min():.1f}%<br>
                <strong>📊 Average Duty:</strong> {preview_df['DutyCycle'].mean():.1f}%
            </div>
            """, unsafe_allow_html=True)
           
            # Enhanced preview graph with better styling
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(preview_df["time"], preview_df["DutyCycle"],
                   color="#185a9d", linewidth=2.5, marker='o', markersize=3, alpha=0.9)
            ax.set_xlabel("Time (s)", fontsize=11, fontweight='bold')
            ax.set_ylabel("Duty Cycle (%)", fontsize=11, fontweight='bold')
            ax.set_title("Complete Drive Cycle Preview", fontsize=12, fontweight='bold')
            ax.grid(True, alpha=0.3)
            ax.fill_between(preview_df["time"], preview_df["DutyCycle"], alpha=0.2, color="#185a9d")
           
            # Add statistics to the plot
            avg_duty = preview_df['DutyCycle'].mean()
            max_duty = preview_df['DutyCycle'].max()
            min_duty = preview_df['DutyCycle'].min()
           
            ax.axhline(y=avg_duty, color='red', linestyle='--', alpha=0.7,
                      label=f'Average: {avg_duty:.1f}%')
            ax.axhline(y=max_duty, color='orange', linestyle=':', alpha=0.6,
                      label=f'Max: {max_duty:.1f}%')
            ax.axhline(y=min_duty, color='green', linestyle=':', alpha=0.6,
                      label=f'Min: {min_duty:.1f}%')
           
            ax.legend(loc='upper right', fontsize=9)
            ax.set_ylim(min_duty * 0.9, max_duty * 1.1)
           
            # Styling
            fig.patch.set_facecolor('#f8f9fa')
            ax.set_facecolor('#ffffff')
           
            st.pyplot(fig)
            plt.close(fig)
           
        except Exception as e:
            st.markdown(f"""
            <div style="text-align: center; padding: 1.5rem; background-color: #f8d7da; border-radius: 10px; border-left: 5px solid #dc3545;">
                <h4>⚠️ Preview Error</h4>
                <p>Cannot preview file: {str(e)}</p>
                <p><small>Please ensure the file contains 'time' and 'DutyCycle' columns</small></p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="text-align: center; padding: 1.5rem; background-color: #d1ecf1; border-radius: 10px; border-left: 5px solid #17a2b8;">
            <h4>📄 No File Selected</h4>
            <p>Select an Excel file to see the drive cycle preview graph</p>
            <p><small>The preview will show the complete drive cycle pattern</small></p>
        </div>
        """, unsafe_allow_html=True)


st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: #666; margin-top: 2rem; font-size: 0.9rem;'>
        🚗 <strong>Drive Train Control System</strong> | Built with Streamlit & GPIOZero | 🍓 Raspberry Pi 5 Compatible
        <br><small>Raspberry Pi 5 drive cycle player for EV battery testing</small>
    </div>
    """,
    unsafe_allow_html=True
)
