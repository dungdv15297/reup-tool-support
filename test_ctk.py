import customtkinter as ctk
app = ctk.CTk()
app.geometry("200x200")
label = ctk.CTkLabel(app, text="Hello")
label.pack()
# app.mainloop() # We just want to see if it initializes without aborting
print("Initialized OK")
