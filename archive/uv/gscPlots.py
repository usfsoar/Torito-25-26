"""
Standalone animated demo: 2×2 plots (pressure, thrust, temp, valve 6) with synthetic data.
For the live GSC UI with all 6 valves and real data/serial hooks, use gsc_dashboard.py.
"""
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np

# Demo length (frames); dt = 0.1 s per frame
DEMO_FRAMES = 400

if __name__ == "__main__":
    # Synthetic time-series (same layout as gsc_dashboard / plottingShit)
    t_array = np.arange(0, 30, 0.1)
    temp_array = np.arange(30, 90, 0.2)
    force_array = np.array([100 * round(abs(x)) + 100 for x in np.sin(t_array)])
    pressure_array = np.arange(900, 0, -3)
    state6_array = np.random.randint(0, 2, size=300)

    plt.style.use("dark_background")
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))

    # Fixed axis limits and titles (reapplied after each cla() in the loop)
    axes[0, 0].set_ylim(0, 1000)
    axes[0, 0].set_title("Pressure (Pa)")
    axes[0, 1].set_ylim(0, 300)
    axes[0, 1].set_title("Thrust (N)")
    axes[1, 0].set_ylim(0, 100)
    axes[1, 0].set_title("Temperature (°C)")
    axes[1, 1].set_title("Solenoid valve 6")
    plt.pause(0.1)

    for i in range(DEMO_FRAMES):
        t = t_array[: i + 1]
        temp = temp_array[: i + 1]
        force = force_array[: i + 1]
        pressure = pressure_array[: i + 1]
        state6 = state6_array[: i + 1][-1]

        # Clear time-series axes only; redraw so lines don’t accumulate
        for ax in [axes[0, 0], axes[0, 1], axes[1, 0]]:
            ax.cla()
        axes[0, 0].set_ylim(0, 1000)
        axes[0, 0].set_title("Pressure (Pa)")
        axes[0, 1].set_ylim(0, 300)
        axes[0, 1].set_title("Thrust (N)")
        axes[1, 0].set_ylim(0, 100)
        axes[1, 0].set_title("Temperature (°C)")
        axes[0, 0].set_xlim(0, max(i * 0.1, 30))
        axes[0, 1].set_xlim(0, max(i * 0.1, 30))
        axes[1, 0].set_xlim(0, max(i * 0.1, 30))

        axes[0, 0].plot(t, pressure, color="b")
        axes[0, 1].plot(t, force, color=(1, 0.647, 0))
        axes[1, 0].plot(t, temp, color="r")

        axes[1, 1].cla()
        axes[1, 1].bar([1], [state6], color="g" if state6 else "darkred", width=0.4)
        axes[1, 1].set_title("Solenoid valve 6")
        axes[1, 1].set(ylim=(0, 1), xticks=([1]), xticklabels=("Valve 6",))
        axes[1, 1].set_yticks([0, 1])
        axes[1, 1].set_yticklabels(["OFF", "ON"])

        plt.draw()
        plt.pause(0.1)
