import pandas as pd
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np

# def plot_data(data):
#     fig, axes = plt.subplots(2,2,figsize=(10,10))
#     data = data.to_numpy()
    
#     axes[0,0].plot(t, temp)
#     axes[0,0].set_title('Temperature (C)')
#     axes[0,1].plot(t,force)
#     axes[0,0].set_title('Temperature (C)')
#     axes[1,0].plot(t, pressure)
#     axes[1,0].set_title('Pressure (Pa)')
#     axes[1,1].bar([1,2,3,4,5,6], 1)
#     axes[1,1].set_title('State')

#     plt.show()



if __name__ == '__main__':

    # test code only dont actually use

    t_array = np.arange(0, 30, 0.1)
    temp_array = np.arange(30, 90, 0.2)
    force_array = np.array([100*round(abs(x)) + 100 for x in np.sin(t_array)])
    pressure_array = np.arange(900,0,-3)
    state1_array = np.random.randint(0,2,size=300)
    state2_array = np.random.randint(0,2,size=300)
    state3_array = np.random.randint(0,2,size=300)
    state4_array = np.random.randint(0,2,size=300)
    state5_array = np.random.randint(0,2,size=300)
    state6_array = np.random.randint(0,2,size=300)

    
    # real code kinda
    """
    fig, axes = plt.subplots(2,2)
    axes[0,0].ylim = (0,100)
    axes[0,0].set_title('Temperature (C)')
    axes[0,1].ylim = (0,300)
    axes[0,0].set_title('Temperature (C)')
    axes[1,0].ylim = (0,1000)
    axes[1,0].set_title('Pressure (Pa)')
    axes[1,1].set_title('State')
    plt.pause(1)

    df = pd.readcsv(csv_file_path)
    data = df.to_numpy()

    while(True):
        df = pd.readcsv(csv_file_path)
        data = df.to_numpy()
        t = data[:,0]
        temp = data[:,1]
        force = data[:,2]
        pressure = data[:,3]
        state1 = data[-1,4]
        state2 = data[-1,5]
        state3 = data[-1,6]
        state4 = data[-1,7]
        state5 = data[-1,8]
        state6 = data[-1,9]

        axes[0,0].plot(t,temp)
        axes[0,1].plot(t,force)
        axes[1,0].plot(t,pressure)
        axes[1,1].bar([1,2,3,4,5,6], [state1, state2, state3, state4, state5, state6])
        axes[1,1].set(ylim=(0,1))
        plt.draw()
        plt.pause(1)
    """
    

    # fake code
    plt.style.use('dark_background')
    # use a reasonable figure size and constrained_layout so subplots resize to available space
    fig, axes = plt.subplots(2,2, figsize=(10,7))
    axes[0,0].set_ylim(0,100)
    axes[0,0].set_title('Temperature (C)')
    axes[0,1].set_ylim(0,300)
    axes[0,1].set_title('Force (N)')
    axes[1,0].set_ylim(0,1000)
    axes[1,0].set_title('Pressure (Pa)')
    axes[1,1].set_title('State')
    plt.pause(0.1)     
    for i in range(400):
        axes[0,0].set_xlim(0,max(i*0.1,30))
        axes[0,1].set_xlim(0,max(i*0.1,30))
        axes[1,0].set_xlim(0,max(i*0.1,30))
        t = t_array[:i+1]
        temp = temp_array[:i+1]
        temp2 = temp_array[:i+1] + 20
        temp3 = temp_array[:i+1] + 40
        force = force_array[:i+1]
        force2 = force_array[:i+1] + 20
        force3 = force_array[:i+1] + 40
        pressure = pressure_array[:i+1]
        pressure2 = pressure_array[:i+1] + 20
        pressure3 = pressure_array[:i+1] + 40
        state1 = state1_array[:i+1][-1]
        state2 = state2_array[:i+1][-1]
        state3 = state3_array[:i+1][-1]
        state4 = state4_array[:i+1][-1]
        state5 =state5_array[:i+1][-1]
        state6 = state6_array[:i+1][-1]

        axes[0,0].plot(t,temp,color='r')
        axes[0,0].plot(t,temp2, color='y')
        axes[0,0].plot(t,temp3, color='y')
        axes[0,1].plot(t,force,color= [1,0.647,0])
        axes[0,1].plot(t,force2,color= [1,0.647,0])
        axes[0,1].plot(t,force3,color= [1,0.647,0])
        axes[1,0].plot(t,pressure,color='b')
        axes[1,0].plot(t,pressure2,color='b')
        axes[1,0].plot(t,pressure3,color='b')
        # Clear the previous bar plot so the axes doesn't accumulate bars each loop
        axes[1,1].cla()
        axes[1,1].bar([1,2,3,4,5,6], [state1, state2, state3, state4, state5, state6], color='g')
        # reapply title/limits/ticks after clearing
        axes[1,1].set_title('State')
        axes[1,1].set(ylim=(0,1), xticks=(np.arange(1,7)))

        plt.draw()
        plt.pause(0.1)
        
