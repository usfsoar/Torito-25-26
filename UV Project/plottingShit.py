"""
Minimal static snapshot: 2×2 plots (pressure, thrust, temp, valve 6).
One-shot plot; no animation. For live dashboard use gsc_dashboard.py; for animated demo use gscPlots.py.
"""
import matplotlib.pyplot as plt
import numpy as np

# Placeholder data (replace with CSV/serial for real use)
t = np.linspace(0, 10, 50)
pressure = 900 - t * 30
thrust = 100 + 80 * np.sin(t * 0.5)
temp = 30 + t * 2
valve6_on = 1  # 0 = OFF, 1 = ON

fig, ax = plt.subplots(2, 2, figsize=(8, 6))
ax[0, 0].plot(t, pressure, "b-")
ax[0, 0].set_title("Pressure (Pa)")
ax[0, 0].set_ylim(0, 1000)

ax[0, 1].plot(t, thrust, color=(1, 0.65, 0))
ax[0, 1].set_title("Thrust (N)")
ax[0, 1].set_ylim(0, 300)

ax[1, 0].plot(t, temp, "r-")
ax[1, 0].set_title("Temperature (°C)")
ax[1, 0].set_ylim(0, 100)

ax[1, 1].bar([1], [valve6_on], color="g" if valve6_on else "darkred", width=0.4)
ax[1, 1].set_title("Solenoid valve 6")
ax[1, 1].set_ylim(0, 1)
ax[1, 1].set_xticks([1])
ax[1, 1].set_xticklabels(["Valve 6"])
ax[1, 1].set_yticks([0, 1])
ax[1, 1].set_yticklabels(["OFF", "ON"])

plt.tight_layout()
plt.show()
