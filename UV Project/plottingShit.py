import matplotlib.pyplot as pl
import pandas as pd
import numpy as np

data_in = [1,2,3,4,5,2,3,4,1,2,3]
fig, ax = pl.subplots(2,2)
ax[0,0].plot(data_in,list(range(11)))
ax[0,1].plot(data_in,list(range(11)))
ax[1,0].plot(data_in,list(range(11)))
ax[1,1].plot(data_in,list(range(11)))
pl.show()

