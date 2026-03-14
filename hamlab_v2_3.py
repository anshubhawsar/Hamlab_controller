import customtkinter as ctk
from PIL import Image, ImageTk
import math
import collections
import random
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
from tkinter import messagebox, filedialog
import tkinter.ttk as ttk 
import webbrowser
import os
import sys
import re
import json
import threading
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

# --- THEME CONFIGURATION ---
APP_NAME = "HAM LAB SMART CONTROLLER"
APP_VERSION = "v2.3"
GITHUB_REPO = "anshubhawsar/Hamlab_controller"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
DOC_PDF_PATH = os.path.join(BASE_DIR, "docs", "HAMLAB_Documentation.pdf")
LOGO_PATH = os.path.join(BASE_DIR, "image.png")

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# --- UNIFIED COLOR PALETTE (Classic Light Theme) ---
COLOR_BG_PRIMARY = "#f8f9fa"      # Light background
COLOR_BG_SECONDARY = "#ffffff"    # Card/panel background
COLOR_BG_SIDEBAR = "#e8eef5"      # Sidebar background
COLOR_BORDER_LIGHT = "#d0d0d0"    # Border color

# Text colors
COLOR_TEXT_PRIMARY = "#1a1a1a"    # Main text
COLOR_TEXT_SECONDARY = "#555555"  # Secondary text
COLOR_TEXT_DIM = "#888888"        # Dim text

# Accent colors
COLOR_ACCENT_BLUE = "#0066cc"     
COLOR_ACCENT_GREEN = "#00aa00"    
COLOR_ACCENT_ORANGE = "#ff8800"   
COLOR_ACCENT_RED = "#cc0000"      

# Legacy colors
COLOR_NEON_BLUE = "#0066cc"
COLOR_NEON_GREEN = "#00aa00"
COLOR_NEON_RED = "#cc0000"
COLOR_NEON_ORANGE = "#ff8800"
COLOR_NEON_PURPLE = "#9900cc"

COLOR_BUTTON_PRIMARY = "#0066cc"
COLOR_BUTTON_SUCCESS = "#00aa00"
COLOR_BUTTON_WARNING = "#ff8800"
COLOR_BUTTON_DANGER = "#cc0000"

# --- ENERGY FORMATTING HELPER ---
def format_energy(value_j: float, per_mm: bool = False) -> str:
    abs_v = abs(value_j)
    if abs_v >= 1e6:
        val = value_j / 1e6
        unit = "MJ"
    elif abs_v >= 1e3:
        val = value_j / 1e3
        unit = "kJ"
    else:
        val = value_j
        unit = "J"

    if per_mm:
        unit = f"{unit}/mm"

    return f"{val:.2f} {unit}"

# --- UI COMPONENTS ---
class GlassCard(ctk.CTkFrame):
    """ A custom frame that mimics a glass panel with enhanced glassy header """
    def __init__(self, master, title=None, color=COLOR_ACCENT_BLUE, **kwargs):
        super().__init__(master, fg_color=COLOR_BG_SECONDARY, corner_radius=12, 
                         border_width=2, border_color=COLOR_BORDER_LIGHT, **kwargs)
        
        if title:
            header_frame = ctk.CTkFrame(self, fg_color=color, corner_radius=10, height=50)
            header_frame.pack(fill="x", padx=0, pady=0)
            header_frame.pack_propagate(False)
            
            ctk.CTkLabel(header_frame, text=title, font=("Arial", 15, "bold"), 
                         text_color="white", fg_color="transparent").pack(anchor="w", padx=16, pady=8)


# --- DATA CLASSES FOR WAAM ---
@dataclass
class WAAMResult:
    power_effective: float
    linear_energy: float
    deposition_rate_kg_h: float
    wire_feed_speed: float
    total_time_min: float
    total_layers: int
    total_mass_kg: float
    heat_warning: bool
    layer_data: list
    total_power: float
    single_layer_energy: float
    totalenergyfornlayers: float
    vol_per_layer_mm3: float
    vol_wire_per_layer: float
    calc_bead_width: float 

class WAAMCalculator:
    def calculate(self, volts, amps, eff, speed_mm_min, wire_d, layer_h, target_h, bead_len, density_g_cm3, target_width_mm):
        # 1. Base Physics
        total_power = volts * amps
        P_eff = total_power * eff
        
        # Speed conversion: mm/min -> mm/s
        speed_mm_s = speed_mm_min / 60.0
        
        # Linear Energy: J/mm
        E_linear = (P_eff / speed_mm_s) if speed_mm_s > 0 else 0
        
        # Single Layer Energy
        single_layer_energy = E_linear * bead_len

        # 2. Geometry & WFS Calculation (Swapped Logic)
        # Old Logic: Input WFS -> Calc Width
        # New Logic: Input Width -> Calc WFS
        
        # Reversing the shape factor assumption: 
        # Previously: Width = 0.75 * (Area / LayerH)
        # Now: Area = (Width * LayerH) / 0.75
        if layer_h > 0:
             bead_cross_section_area = (target_width_mm * layer_h) / 0.75
        else:
             bead_cross_section_area = 0
             
        # Volumetric Deposition Rate (mm^3/min) = CrossSectionArea * TravelSpeed
        vol_rate_mm3_min = bead_cross_section_area * speed_mm_min
        
        # Wire Area Aw = pi * (d/2)^2
        A_wire = math.pi * ((wire_d / 2)**2)
        
        # Calculate Required WFS
        if A_wire > 0:
            wfs_mm_min = vol_rate_mm3_min / A_wire
            wfs_m_min = wfs_mm_min / 1000.0
        else:
            wfs_m_min = 0

        # Volume per layer = CrossSection * Length
        vol_pass_mm3 = 0.75 * bead_cross_section_area * bead_len
        vol_wire_per_layer = bead_cross_section_area * bead_len
        
        # 3. Layer Count Logic
        if layer_h > 0:
            layers = int(target_h / layer_h)
        else:
            layers = 0
            
        # Total Energy
        totalenergyfornlayers = single_layer_energy * layers

        # 4. Time Calculation
        # Time per pass (s) = L / v
        time_pass_s = bead_len / speed_mm_s if speed_mm_s > 0 else 0
        total_time_min = (time_pass_s * layers) / 60.0 

        # 5. Mass Calculation
        total_vol_mm3 = vol_pass_mm3 * layers
        density_kg_mm3 = density_g_cm3 * 1e-6
        total_mass_kg = total_vol_mm3 * density_kg_mm3

        # 6. Deposition Rate (kg/h)
        total_time_h = total_time_min / 60.0
        dep_rate = (total_mass_kg / total_time_h) if total_time_h > 0 else 0

        # 7. Generate Schedule
        layer_schedule = []
        cum_height = 0
        cum_energy = 0
        cum_time = 0
        
        for i in range(1, layers + 1):
            cum_height += layer_h
            cum_energy += single_layer_energy 
            cum_time += (time_pass_s / 60.0)
            
            layer_schedule.append({
                "id": i,
                "height": cum_height,
                "energy_kJ": single_layer_energy / 1000.0,
                "cum_energy_kJ": cum_energy / 1000.0,
                "time_min": cum_time
            })

        return WAAMResult(
            power_effective=P_eff,
            linear_energy=E_linear,
            deposition_rate_kg_h=dep_rate,
            wire_feed_speed=wfs_m_min,
            total_time_min=total_time_min,
            total_layers=layers,
            total_mass_kg=total_mass_kg,
            heat_warning=(E_linear > 800),
            layer_data=layer_schedule,
            total_power=total_power,
            single_layer_energy=single_layer_energy,
            totalenergyfornlayers=totalenergyfornlayers,
            vol_per_layer_mm3=vol_pass_mm3,
            vol_wire_per_layer=vol_wire_per_layer,
            calc_bead_width=target_width_mm # Just return input for report consistency
        )


class FSWPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        
        # Grid Configuration for HMI consistency
        self.columnconfigure(0, weight=1, minsize=480)
        self.columnconfigure(1, weight=1, minsize=480)
        self.rowconfigure(0, weight=1)
        
        # --- RESEARCH DATA ---
        self.MAT_DB = {
            "Al 6061-T6":   {"Mu": 0.45, "Base_Fl": 1330.0, "Base_Ft": 135.0}, 
            "Al 2195":      {"Mu": 0.38, "Base_Fl": 4100.0, "Base_Ft": 200.0}, 
            "Mild Steel":   {"Mu": 0.30, "Base_Fl": 3500.0, "Base_Ft": 180.0},
            "Copper":       {"Mu": 0.35, "Base_Fl": 2200.0, "Base_Ft": 150.0},
        }

        # ================= LEFT COLUMN: INPUTS =================
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)

        # 1. MATERIAL CONFIGURATION
        card_mat = GlassCard(left_frame, title="1️⃣ MATERIAL CONFIGURATION", color="#0066cc")
        card_mat.pack(fill="x", pady=(0, 12))
        
        self.add_label(card_mat, "Workpiece Material")
        self.opt_workpiece = ctk.CTkOptionMenu(card_mat, values=list(self.MAT_DB.keys()), 
                                               command=self.update_material_defaults,
                                               fg_color="#e8eef5", button_color="#0066cc", 
                                               text_color="#1a1a1a", button_hover_color="#0052a3")
        self.opt_workpiece.pack(fill="x", padx=20, pady=5)

        # 2. TOOL GEOMETRY
        card_geo = GlassCard(left_frame, title="2️⃣ TOOL GEOMETRY", color="#ff8800")
        card_geo.pack(fill="x", pady=12)
        self.ent_ds = self.add_input_row(card_geo, "Shoulder Dia (mm)", "19.2") 
        self.ent_dp = self.add_input_row(card_geo, "Pin Dia (mm)", "6.35")      
        self.ent_pl = self.add_input_row(card_geo, "Pin Length (mm)", "5.83")   

        # 3. WELDING PARAMETERS
        card_proc = GlassCard(left_frame, title="3️⃣ WELDING PARAMETERS", color="#00aa00")
        card_proc.pack(fill="x", pady=12)
        self.ent_rpm = self.add_input_row(card_proc, "Rotation Speed (RPM)", "800")
        self.ent_speed = self.add_input_row(card_proc, "Weld Speed (mm/s)", "2.0")
        self.ent_force = self.add_input_row(card_proc, "Downward Force (kN)", "12.5")
        self.ent_plunge = self.add_input_row(card_proc, "Plunge Depth (mm)", "0.89") 
        
        # Hidden Internal Storage
        self.ent_mu = ctk.CTkEntry(card_mat, width=0, height=0) 
        self.ent_mu.insert(0, "0.45") 

        # ================= RIGHT COLUMN: DASHBOARD =================
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)

        # 4. PHYSICS RESULTS
        card_res = GlassCard(right_frame, title="📊 SIMULATION OUTPUTS", color="#00aa00")
        card_res.pack(fill="x", pady=(0, 12))
        
        self.lbl_torque = self.add_stat(card_res, "REQ. TORQUE", "0.00 Nm")
        self.lbl_flong  = self.add_stat(card_res, "LONG. FORCE (Fl)", "0 N")
        self.lbl_ftrans = self.add_stat(card_res, "TRANS. FORCE (Ft)", "0 N")
        self.lbl_energy = self.add_stat(card_res, "SPECIFIC ENERGY", "0 J/mm")
        self.lbl_power  = self.add_stat(card_res, "HEAT GEN (Q)", "0 W")

        # BUTTONS FRAME
        btns_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        btns_frame.pack(fill="x", pady=(20, 0))
        btns_frame.columnconfigure(0, weight=1)
        btns_frame.columnconfigure(1, weight=1)

        btn_calc = ctk.CTkButton(btns_frame, text="▶ RUN SIMULATION", height=60, 
                                 fg_color="#00aa00", hover_color="#008800",
                                 text_color="white", font=("Arial", 16, "bold"),
                                 command=self.calculate_physics, corner_radius=12)
        btn_calc.grid(row=0, column=0, sticky="ew", padx=5)

        self.last_fsw_results = None
        self.update_material_defaults("Al 6061-T6")

    # --- UI HELPERS ---
    def add_label(self, parent, text):
        ctk.CTkLabel(parent, text=text, text_color="#555555", font=("Arial", 12, "bold"), anchor="w").pack(fill="x", padx=24, pady=(8, 2))

    def add_input_row(self, parent, label, default):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f, text=label, text_color="#1a1a1a", font=("Arial", 13)).pack(side="left")
        
        e = ctk.CTkEntry(f, width=120, justify="center", 
                         fg_color="#ffffff", text_color="#1a1a1a", 
                         border_width=2, border_color="#d0d0d0", 
                         corner_radius=6)
        e.pack(side="right")
        e.insert(0, default)
        return e

    def add_stat(self, parent, label, default):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=8)
        ctk.CTkLabel(f, text=label, text_color="#555555", font=("Arial", 12, "bold")).pack(side="left")
        l = ctk.CTkLabel(f, text=default, text_color="#0066cc", font=("Arial", 22, "bold"))
        l.pack(side="right")
        return l

    def update_material_defaults(self, selection):
        if selection in self.MAT_DB:
            self.ent_mu.delete(0, 'end')
            self.ent_mu.insert(0, str(self.MAT_DB[selection]["Mu"]))

    # --- CALCULATION ENGINE ---
    def calculate_physics(self):
        try:
            # 1. Gather & Clean Inputs
            rpm = float(str(self.ent_rpm.get()).strip())
            v_weld = float(str(self.ent_speed.get()).strip())  # mm/s
            F_down_kN = float(str(self.ent_force.get()).strip())
            plunge_mm = float(str(self.ent_plunge.get()).strip())
            mu = float(str(self.ent_mu.get()).strip()) 
            
            Ds = float(str(self.ent_ds.get()).strip()) / 1000.0 # m
            mat_name = self.opt_workpiece.get()
            mat_data = self.MAT_DB[mat_name]

            # 2. Physics Model (Melendez et al.)
            F_down_N = F_down_kN * 1000.0
            omega = (2 * math.pi * rpm) / 60.0
            
            # --- Torque (T) ---
            Rs = Ds / 2.0
            softening_modifier = max(0.5, 1.0 - (rpm / 3500.0))
            torque = (2.0/3.0) * mu * softening_modifier * F_down_N * Rs
            
            # --- Power & Heat Generation (Q) ---
            power = torque * omega
            
            # --- Longitudinal Force (Fl) ---
            plunge_ratio = plunge_mm / 0.89
            speed_ratio = v_weld / 2.0
            f_longitudinal = mat_data["Base_Fl"] * plunge_ratio * speed_ratio
            
            # --- Transverse Force (Ft) ---
            f_transverse = mat_data["Base_Ft"] * speed_ratio

            # --- Specific Weld Energy (Es) ---
            energy_j_mm = power / v_weld if v_weld > 0 else 0

            # 3. Update Dashboard Labels
            self.lbl_torque.configure(text=f"{torque:.2f} Nm")
            self.lbl_power.configure(text=f"{int(power)} W")
            self.lbl_flong.configure(text=f"{int(f_longitudinal)} N")
            self.lbl_ftrans.configure(text=f"{int(f_transverse)} N")
            self.lbl_energy.configure(text=f"{int(energy_j_mm)} J/mm")

            # Store for Report
            self.last_fsw_results = {
                'inputs': {
                    'material': mat_name,
                    'ds': self.ent_ds.get(), 'dp': self.ent_dp.get(), 'pl': self.ent_pl.get(),
                    'rpm': self.ent_rpm.get(), 'speed': self.ent_speed.get(), 'force': self.ent_force.get()
                },
                'outputs': {
                    'torque': torque, 'power': power,
                    'f_long': f_longitudinal, 'f_trans': f_transverse,
                    'energy': energy_j_mm
                }
            }

        except Exception as e:
            messagebox.showerror("Simulation Error", str(e))


class PMConsolidationPanel(ctk.CTkScrollableFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent", orientation="vertical", width=900, height=600)

        # --- Layout Configuration ---
        self.columnconfigure(0, weight=1, minsize=420)
        self.columnconfigure(1, weight=1, minsize=420)
        # Create a style object
        style = ttk.Style()

        # 1. Configure the Rows (Data)
        style.configure("Treeview", font=("Arial", 12), rowheight=30) 

        # 2. Configure the Headings (Titles)
        style.configure("Treeview.Heading", font=("Arial", 12, "bold"))
        
        # ==========================================================
        # TOP LEFT: Inputs & Summary
        # ==========================================================
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # 1. Inputs
        card_inputs = GlassCard(left_frame, title="1️⃣ PM CONSOLIDATION INPUTS")
        card_inputs.pack(fill="x", pady=(0, 10))

        self.ent_L = self.add_input_row(card_inputs, "Length L (mm)", "20.0")
        self.ent_W = self.add_input_row(card_inputs, "Width W (mm)", "18.0")
        self.ent_H = self.add_input_row(card_inputs, "Target Height H (mm)", "100.0")
        
        # New Input: Compression Ratio
        self.ent_cr = self.add_input_row(card_inputs, "Compression Ratio", "2.0")
        
        self.ent_alpha = self.add_input_row(card_inputs, "Consolidation Resist (alpha)", "0.03")
        self.ent_beta = self.add_input_row(card_inputs, "Geometric Distort (beta)", "0.05")
        self.ent_Pbase = self.add_input_row(card_inputs, "Base Pressure (MPa)", "300")
        self.ent_h_dep = self.add_input_row(card_inputs, "Deposition Height (mm)", "2.0")
        self.ent_h_mach = self.add_input_row(card_inputs, "Machining Cut (mm)", "0.5")

        # 2. Summary Metrics
        summary_card = GlassCard(left_frame, title="📈 SUMMARY METRICS")
        summary_card.pack(fill="x", pady=(5, 0))
        
        self.lbl_stroke = self.add_stat(summary_card, "Compression Stroke (mm)", "0.0")
        self.lbl_max_force = self.add_stat(summary_card, "Max Peak Force (kN)", "0.0")
        self.lbl_total_force = self.add_stat(summary_card, "Total Cum. Force (kN)", "0.0") 
        self.lbl_avg_pressure = self.add_stat(summary_card, "Avg Pressure (MPa)", "0.0")
        self.lbl_cum_work = self.add_stat(summary_card, "Total Work (kJ)", "0.0")

        # ==========================================================
        # TOP RIGHT: Results Table
        # ==========================================================
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        card_results = GlassCard(right_frame, title="📊 PM CONSOLIDATION RESULTS")
        card_results.pack(fill="both", expand=True)

        self.table_container = ctk.CTkFrame(card_results, fg_color="transparent")
        self.table_container.pack(fill="both", expand=True, padx=5, pady=5)

        cols = ("Layer", "Force_kN", "F_Dec_%", "Energy_J", "E_Dec_%", "Press_MPa")
        
        vsb = ttk.Scrollbar(self.table_container, orient="vertical")
        hsb = ttk.Scrollbar(self.table_container, orient="horizontal")

        self.tree = ttk.Treeview(self.table_container, columns=cols, show='headings', height=14,
                                 yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        # Setup column headers
        for c in cols:
            heading = c.replace('_', ' ').replace('Dec', 'Dec')
            self.tree.heading(c, text=heading)
            self.tree.column(c, anchor='center', stretch=True, width=80)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        self.table_container.grid_columnconfigure(0, weight=1)
        self.table_container.grid_rowconfigure(0, weight=1)

        self.pm_msg = ctk.CTkLabel(card_results, text="", text_color=COLOR_TEXT_SECONDARY)
        self.pm_msg.pack(fill="x", padx=8, pady=2)

        # ==========================================================
        # MIDDLE BOTTOM: Graph (Split into two subplots)
        # ==========================================================
        plot_card = GlassCard(self, title="📉 PHYSICS PROFILES", color=COLOR_NEON_BLUE)
        plot_card.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=(10, 10))
        
        self.fig_pm, (self.ax_force, self.ax_energy) = plt.subplots(1, 2, dpi=80)
        self.fig_pm.set_size_inches(10, 4) 
        self.fig_pm.subplots_adjust(wspace=0.3, bottom=0.15) 
        
        self.pm_canvas = FigureCanvasTkAgg(self.fig_pm, master=plot_card)
        self.pm_canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

        # ==========================================================
        # FOOTER: Buttons
        # ==========================================================
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 20))
        
        btn_calc = ctk.CTkButton(btn_frame, text="CALCULATE PM", fg_color=COLOR_NEON_GREEN, command=self.calculate_pm)
        btn_calc.pack(side="left", padx=6)
        
        btn_report = ctk.CTkButton(btn_frame, text="GENERATE REPORT", fg_color=COLOR_NEON_BLUE, command=self.generate_report)
        btn_report.pack(side="left", padx=6)

        self._enable_scrolling(self)

    def _enable_scrolling(self, widget):
        widget.bind("<MouseWheel>", self._on_mouse_wheel, add="+") 
        widget.bind("<Button-4>", self._on_mouse_wheel, add="+")
        widget.bind("<Button-5>", self._on_mouse_wheel, add="+")
        
        for child in widget.winfo_children():
            if isinstance(child, (ttk.Treeview, ttk.Scrollbar)):
                continue
            self._enable_scrolling(child)

    def _on_mouse_wheel(self, event):
        if event.num == 5 or event.delta < 0:
            scroll_dir = 1
        else:
            scroll_dir = -1
        try:
            self._parent_canvas.yview_scroll(scroll_dir*30, "units")
        except:
            pass

    def add_input_row(self, parent, label, default):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=5)
        lbl = ctk.CTkLabel(f, text=label, text_color=COLOR_TEXT_PRIMARY, font=("Arial", 13, "bold"))
        lbl.pack(side="left")
        e = ctk.CTkEntry(f, width=200, justify="center", fg_color="white", text_color=COLOR_TEXT_PRIMARY, border_color=COLOR_BORDER_LIGHT, border_width=2, font=("Arial", 12), corner_radius=6)
        e.pack(side="right", padx=8)
        e.insert(0, default)
        
        self._enable_scrolling(f)
        self._enable_scrolling(lbl)
        self._enable_scrolling(e)
        return e

    def add_stat(self, parent, label, default):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=4)
        lbl = ctk.CTkLabel(f, text=label, text_color=COLOR_TEXT_SECONDARY, font=("Arial", 12, "bold"))
        lbl.pack(side="left")
        val = ctk.CTkLabel(f, text=default, text_color=COLOR_NEON_BLUE, font=("Arial", 16, "bold"))
        val.pack(side="right", padx=8)
        
        self._enable_scrolling(f)
        self._enable_scrolling(lbl)
        self._enable_scrolling(val)
        return val

    def calculate_pm(self):
        try:
            # Inputs
            L = float(self.ent_L.get())
            W = float(self.ent_W.get())
            H_target = float(self.ent_H.get())
            CR = float(self.ent_cr.get()) 
            alpha = float(self.ent_alpha.get())
            beta = float(self.ent_beta.get())
            P_base = float(self.ent_Pbase.get())
            h_dep = float(self.ent_h_dep.get())
            h_mach = float(self.ent_h_mach.get())

            A_base = L * W
            h_eff = h_dep - h_mach
            N = math.ceil(H_target / h_eff) if h_eff > 0 else 0
            
            stroke_mm = h_dep * (CR - 1) 

            table = []
            A_prev = None; P_prev = None
            F_prev = None; E_prev = None
            forces = []; energies = []

            for n in range(1, N + 1):
                if n == 1:
                    A_n = A_base
                    P_n = P_base
                else:
                    A_n = A_prev * (1 - beta) 
                    P_n = P_prev * (1 + alpha) 

                F_n = A_n * P_n
                F_kn = F_n / 1000.0
                
                if n == 1:
                    initial_F = F_kn
                    inital_k = F_n * (stroke_mm / 1000.0)

                # Energy Calculation: Work = Force * Displacement
                energy_j = F_n * (stroke_mm / 1000.0) 
                
                # Percent Decrease Calculation (Force & Energy)
                if n == 1:
                    f_dec = 0.0
                    e_dec = 0.0
                else:
                    f_dec = ((initial_F - F_kn) / initial_F * 100.0) if initial_F != 0 else 0.0
                    e_dec = ((inital_k - energy_j) / inital_k * 100.0) if inital_k != 0 else 0.0

                table.append({
                    "layer": n, "area": A_n, "pressure": P_n, 
                    "force_kn": F_kn, "f_dec": f_dec,
                    "energy_j": energy_j, "e_dec": e_dec
                })
                
                forces.append(F_kn)
                energies.append(energy_j)
                
                A_prev = A_n; P_prev = P_n
                F_prev = F_kn; E_prev = energy_j

            # UI Update: Table
            for i in self.tree.get_children(): self.tree.delete(i)
            for r in table:
                self.tree.insert('', 'end', values=(
                    r['layer'], 
                    f"{r['force_kn']:.3f}", f"{r['f_dec']:.2f}%", 
                    f"{r['energy_j']:.2f}", f"{r['e_dec']:.2f}%",
                    f"{r['pressure']:.1f}"
                ))
            
            total_work_kj = sum(energies) / 1000.0
            max_force = max(forces) if forces else 0
            
            pressures = [r['pressure'] for r in table]
            avg_pressure = (sum(pressures) / len(pressures)) if pressures else 0.0

            # UI Update: Metrics
            self.lbl_max_force.configure(text=f"{max_force:.2f} kN")
            self.lbl_stroke.configure(text=f"{stroke_mm:.2f} mm") 
            self.lbl_cum_work.configure(text=f"{total_work_kj:.3f} kJ")
            self.lbl_total_force.configure(text=f"{sum(forces):.2f} kN") 
            self.lbl_avg_pressure.configure(text=f"{avg_pressure:.1f} MPa")

            # UI Update: Plots
            self.ax_force.clear()
            self.ax_energy.clear()
            
            layers = [r['layer'] for r in table]
            f_vals = [r['force_kn'] for r in table]
            e_vals = [r['energy_j'] for r in table]

            # Graph 1: Force
            self.ax_force.set_xlabel('Layer')
            self.ax_force.set_ylabel('Force (kN)', color=COLOR_NEON_BLUE)
            self.ax_force.plot(layers, f_vals, marker='o', color=COLOR_NEON_BLUE)
            self.ax_force.set_title("Force Profile", fontsize=10)
            self.ax_force.grid(True, alpha=0.3)

            # Graph 2: Energy
            self.ax_energy.set_xlabel('Layer')
            self.ax_energy.set_ylabel('Energy (J)', color=COLOR_NEON_ORANGE)
            self.ax_energy.plot(layers, e_vals, color=COLOR_NEON_ORANGE, linestyle='--', marker='x')
            self.ax_energy.set_title("Energy Profile", fontsize=10)
            self.ax_energy.grid(True, alpha=0.3)
            
            self.fig_pm.tight_layout()
            self.pm_canvas.draw()
            
            self.last_pm_results = {
                "table": table,
                "stroke_mm": stroke_mm,
                "max_force_kn": max_force,
                "total_work_kj": total_work_kj,
                "total_force_kn": sum(forces)
            }
            self.pm_msg.configure(text=f"Success: Computed {len(table)} layers")

        except ValueError:
            self.pm_msg.configure(text="Error: Check numeric inputs")
            messagebox.showerror("Error", "Check numeric inputs")

    def generate_report(self):
        if not hasattr(self, 'last_pm_results'): 
            return messagebox.showwarning("Wait", "Run Calculation First")
        
        try:
            filename_ts = datetime.now().strftime('%Y%m%d_%H%M')
            initial_name = f"PM_Report_{filename_ts}.pdf"
            filepath = filedialog.asksaveasfilename(initialfile=initial_name, filetypes=[("PDF Documents", "*.pdf")])
            
            if filepath and FPDF:
                temp_graph_path = "temp_pm_graph.png"
                self.fig_pm.savefig(temp_graph_path, dpi=150, bbox_inches='tight')

                pdf = PDFReport()
                pdf.add_page()
                pdf.section_title("PM CONSOLIDATION REPORT")
                
                pdf.set_font("Arial", "", 10)
                pdf.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, 'R')
                pdf.ln(5)
                
                pdf.set_fill_color(240, 240, 240)
                pdf.cell(0, 8, "  Global Parameters & Results", 0, 1, 'L', True)
                pdf.ln(2)
                
                def add_r(l, v, u=""):
                    pdf.cell(90, 6, f"  {l}", 0, 0)
                    pdf.set_font("Arial", "B", 10)
                    pdf.cell(0, 6, f"{v} {u}", 0, 1)
                    pdf.set_font("Arial", "", 10)
                    pdf.set_draw_color(220)
                    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                    pdf.ln(1)

                add_r("Target Geometry", f"{self.ent_L.get()} x {self.ent_W.get()} x {self.ent_H.get()}", "mm")
                add_r("Compression Ratio", self.ent_cr.get())
                add_r("Compression Stroke", f"{self.last_pm_results['stroke_mm']:.2f}", "mm")
                add_r("Max Force", f"{self.last_pm_results['max_force_kn']:.3f}", "kN")
                add_r("Total Cum. Force", f"{self.last_pm_results['total_force_kn']:.3f}", "kN")
                add_r("Total Energy/Work", f"{self.last_pm_results['total_work_kj']:.3f}", "kJ")
                pdf.ln(5)

                if os.path.exists(temp_graph_path):
                    pdf.set_font("Arial", "B", 11)
                    pdf.cell(0, 8, "Physics Simulation Graphs", 0, 1, 'L')
                    pdf.image(temp_graph_path, x=10, w=190)
                    pdf.ln(5)

                pdf.section_title(f"LAYER DATA (N={len(self.last_pm_results['table'])})")
                
                pdf.set_font("Arial", "B", 9)
                pdf.set_fill_color(0, 102, 204)
                pdf.set_text_color(255)
                
                headers = [("Lay", 15), ("Force(kN)", 30), ("F Dec(%)", 30), 
                           ("Energy(J)", 30), ("E Dec(%)", 30), ("Press(MPa)", 30)]
                
                for h, w in headers:
                    pdf.cell(w, 8, h, 1, 0, 'C', True)
                pdf.ln()
                
                pdf.set_font("Arial", "", 9)
                pdf.set_text_color(0)
                
                for r in self.last_pm_results['table']:
                    if pdf.get_y() > 270:
                        pdf.add_page()
                        pdf.set_font("Arial", "B", 9)
                        pdf.set_fill_color(0, 102, 204)
                        pdf.set_text_color(255)
                        for h, w in headers:
                            pdf.cell(w, 8, h, 1, 0, 'C', True)
                        pdf.ln()
                        pdf.set_font("Arial", "", 9)
                        pdf.set_text_color(0)

                    pdf.cell(15, 7, str(r['layer']), 1, 0, 'C')
                    pdf.cell(30, 7, f"{r['force_kn']:.3f}", 1, 0, 'C')
                    pdf.cell(30, 7, f"{r['f_dec']:.2f}", 1, 0, 'C')
                    pdf.cell(30, 7, f"{r['energy_j']:.2f}", 1, 0, 'C')
                    pdf.cell(30, 7, f"{r['e_dec']:.2f}", 1, 0, 'C')
                    pdf.cell(30, 7, f"{r['pressure']:.1f}", 1, 1, 'C')
                
                pdf.output(filepath)
                webbrowser.open('file://' + filepath)
                self.pm_msg.configure(text=f"Report Saved!")
                
                if os.path.exists(temp_graph_path):
                    os.remove(temp_graph_path)

            elif not FPDF:
                messagebox.showerror("Error", "FPDF library not installed.")
                
        except Exception as e:
            messagebox.showerror("Report Error", str(e))

class HomePanel(ctk.CTkFrame):
    def __init__(self, master, nav_callbacks=None):
        super().__init__(master, fg_color=COLOR_BG_PRIMARY)
        
        self.nav_callbacks = nav_callbacks or {}
        
        content = ctk.CTkFrame(self, fg_color=COLOR_BG_SECONDARY, corner_radius=12, border_width=2, border_color=COLOR_BORDER_LIGHT)
        content.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(content, text="Welcome to HAM Lab", font=("Arial", 32, "bold"), 
                     text_color=COLOR_TEXT_PRIMARY).pack(pady=(20, 5))
        
        ctk.CTkLabel(content, text="Advanced Physics Engine for Welding & Additive Manufacturing", 
                     font=("Arial", 15, "bold"), text_color=COLOR_TEXT_SECONDARY).pack(pady=(5, 20))
        
        features_frame = ctk.CTkFrame(content, fg_color="transparent")
        features_frame.pack(pady=20, padx=30, fill="both", expand=True)
        
        features = [
            ("⚙️ FSW Process", "Friction Stir Welding parameters and physics simulation", self.nav_callbacks.get("fsw")),
            ("⚡ WAAM Energy", "Wire Arc Additive Manufacturing calculations", self.nav_callbacks.get("waam")),
            ("🧪 PM Consolidation", "Powder Metallurgy consolidation analysis", self.nav_callbacks.get("pm")),
            ("🆚 Compare", "Side-by-side process comparison tools", self.nav_callbacks.get("compare")),
            ("📚 Documentation", "Open product guide and technical notes", self.nav_callbacks.get("docs")),
        ]
        
        for i, (title, desc, callback) in enumerate(features):
            row = i // 2
            col = i % 2
            
            card = ctk.CTkFrame(features_frame, fg_color=COLOR_BG_PRIMARY, border_width=2, 
                               border_color=COLOR_ACCENT_BLUE, corner_radius=12)
            card.grid(row=row, column=col, padx=12, pady=12, sticky="nsew")
            
            if callback:
                card.configure(cursor="hand2")
                
                inner_frame = ctk.CTkFrame(card, fg_color="transparent")
                inner_frame.pack(fill="both", expand=True, padx=0, pady=0)
                inner_frame.configure(cursor="hand2")
                
                title_label = ctk.CTkLabel(inner_frame, text=title, font=("Arial", 16, "bold"), text_color=COLOR_ACCENT_BLUE,
                                       fg_color="transparent", cursor="hand2")
                title_label.pack(anchor="w", padx=16, pady=(12, 5))
                
                desc_label = ctk.CTkLabel(inner_frame, text=desc, font=("Arial", 13), text_color=COLOR_TEXT_SECONDARY, wraplength=280,
                                      fg_color="transparent", cursor="hand2")
                desc_label.pack(anchor="w", padx=16, pady=(0, 15))
                
                def make_callback(cb):
                    def on_click(event=None):
                        cb()
                    return on_click
                
                card_callback = make_callback(callback)
                card.bind("<Button-1>", card_callback)
                inner_frame.bind("<Button-1>", card_callback)
                title_label.bind("<Button-1>", card_callback)
                desc_label.bind("<Button-1>", card_callback)
                
                def on_enter(event, c=card):
                    c.configure(fg_color="#e8f4f8", border_color=COLOR_ACCENT_GREEN)
                def on_leave(event, c=card):
                    c.configure(fg_color=COLOR_BG_PRIMARY, border_color=COLOR_ACCENT_BLUE)
                
                card.bind("<Enter>", on_enter)
                card.bind("<Leave>", on_leave)
                inner_frame.bind("<Enter>", on_enter)
                inner_frame.bind("<Leave>", on_leave)
                title_label.bind("<Enter>", on_enter)
                title_label.bind("<Leave>", on_leave)
                desc_label.bind("<Enter>", on_enter)
                desc_label.bind("<Leave>", on_leave)
            else:
                ctk.CTkLabel(card, text=title, font=("Arial", 16, "bold"), text_color=COLOR_ACCENT_BLUE,
                             fg_color="transparent").pack(anchor="w", padx=16, pady=(12, 5))
                ctk.CTkLabel(card, text=desc, font=("Arial", 13), text_color=COLOR_TEXT_SECONDARY, wraplength=280,
                             fg_color="transparent").pack(anchor="w", padx=16, pady=(0, 15))
        
        features_frame.columnconfigure(0, weight=1)
        features_frame.columnconfigure(1, weight=1)


class DocumentationPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=COLOR_BG_PRIMARY)

        shell = ctk.CTkFrame(self, fg_color=COLOR_BG_SECONDARY, corner_radius=12, border_width=2, border_color=COLOR_BORDER_LIGHT)
        shell.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(shell, text="Documentation", font=("Arial", 28, "bold"), text_color=COLOR_TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(20, 4))
        ctk.CTkLabel(shell, text="Open project references and manuals.", font=("Arial", 14), text_color=COLOR_TEXT_SECONDARY).pack(anchor="w", padx=20, pady=(0, 8))

        card = GlassCard(shell, title="Product Documentation", color=COLOR_ACCENT_BLUE)
        card.pack(fill="x", padx=16, pady=12)

        doc_status = "Found" if os.path.exists(DOC_PDF_PATH) else "Missing"
        ctk.CTkLabel(card, text=f"Main PDF: docs/HAMLAB_Documentation.pdf [{doc_status}]", font=("Arial", 13), text_color=COLOR_TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(12, 6))

        ctk.CTkButton(
            card,
            text="Open HAMLAB_Documentation.pdf",
            height=42,
            fg_color=COLOR_BUTTON_PRIMARY,
            hover_color="#0052a3",
            font=("Arial", 13, "bold"),
            command=self.open_pdf,
        ).pack(anchor="w", padx=20, pady=8)

        ctk.CTkButton(
            card,
            text="Open docs folder",
            height=40,
            fg_color=COLOR_BUTTON_SUCCESS,
            hover_color="#008800",
            font=("Arial", 12, "bold"),
            command=self.open_docs_folder,
        ).pack(anchor="w", padx=20, pady=(0, 12))

    def open_pdf(self):
        if not os.path.exists(DOC_PDF_PATH):
            return messagebox.showerror("Missing File", "Documentation PDF not found at docs/HAMLAB_Documentation.pdf")
        webbrowser.open("file://" + DOC_PDF_PATH)

    def open_docs_folder(self):
        docs_dir = os.path.dirname(DOC_PDF_PATH)
        if os.path.exists(docs_dir):
            webbrowser.open("file://" + docs_dir)
        else:
            messagebox.showerror("Missing Folder", "docs folder does not exist.")

# ==============================================================================
# 📄 BACKEND: UNIFIED PDF REPORT ENGINE (FPDF)
# ==============================================================================

if FPDF:
    class PDFReport(FPDF):
        def __init__(self, process_name="PROCESS REPORT"):
            super().__init__()
            self.process_name = process_name
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def header(self):
            # Main Title
            self.set_font('Helvetica', 'B', 20)
            self.set_text_color(0, 51, 102)
            self.cell(0, 10, 'HAM LAB REPORT', 0, 1, 'C')
            
            # Process Name
            self.set_font('Helvetica', 'B', 12)
            self.set_text_color(100, 100, 100)
            self.cell(0, 6, self.process_name, 0, 1, 'C') 
            
            # Time properly
            self.set_font('Helvetica', 'I', 9)
            self.set_text_color(128)
            self.cell(0, 5, f"Generated: {self.timestamp}", 0, 1, 'R')
            
            self.ln(2)
            self.set_draw_color(0, 51, 102); self.set_line_width(0.5)
            self.line(10, self.get_y(), 200, self.get_y()); self.ln(10)

        def footer(self):
            self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(128)
            self.cell(0, 10, f'Page {self.page_no()} | HAM Lab Engineering © 2026', 0, 0, 'C')

        def section_title(self, label):
            self.set_font('Helvetica', 'B', 12); self.set_fill_color(230, 230, 230); self.set_text_color(0)
            self.cell(0, 8, f"  {label}", 0, 1, 'L', True); self.ln(4)

        def add_row(self, label, value, unit=""):
            self.set_font('Helvetica', '', 11); self.set_text_color(50)
            self.cell(90, 7, f"  {label}", 0, 0)
            self.set_font('Helvetica', 'B', 11); self.set_text_color(0)
            self.cell(0, 7, f"{value} {unit}", 0, 1)
            self.set_draw_color(220); self.set_line_width(0.2)
            self.line(10, self.get_y(), 200, self.get_y()); self.ln(1)

        def add_layer_table(self, layer_data):
            self.set_font('Helvetica', 'B', 10)
            self.set_fill_color(0, 51, 102)
            self.set_text_color(255)
            
            self.cell(30, 8, "Layer #", 1, 0, 'C', True)
            self.cell(40, 8, "Z-Height (mm)", 1, 0, 'C', True)
            self.cell(40, 8, "Energy (kJ)", 1, 0, 'C', True)
            self.cell(40, 8, "Cum. Time (min)", 1, 1, 'C', True)
            
            self.set_font('Helvetica', '', 10)
            self.set_text_color(0)
            
            for row in layer_data:
                self.cell(30, 7, str(row['id']), 1, 0, 'C')
                self.cell(40, 7, f"{row['height']:.1f}", 1, 0, 'C')
                self.cell(40, 7, f"{row['energy_kJ']:.2f}", 1, 0, 'C')
                self.cell(40, 7, f"{row['time_min']:.2f}", 1, 1, 'C')
                
                if self.get_y() > 270:
                    self.add_page()
                    self.set_font('Helvetica', 'B', 10)
                    self.set_fill_color(0, 51, 102); self.set_text_color(255)
                    self.cell(30, 8, "Layer #", 1, 0, 'C', True)
                    self.cell(40, 8, "Z-Height (mm)", 1, 0, 'C', True)
                    self.cell(40, 8, "Energy (kJ)", 1, 0, 'C', True)
                    self.cell(40, 8, "Cum. Time (min)", 1, 1, 'C', True)
                    self.set_font('Helvetica', '', 10); self.set_text_color(0)

        def add_pm_table(self, table_data):
            self.set_font('Helvetica', 'B', 10)
            self.set_fill_color(0, 51, 102)
            self.set_text_color(255)
            
            headers = [("Layer", 20), ("Area (mm2)", 35), ("Pressure (MPa)", 35), ("Force (kN)", 30), ("% Inc", 30)]
            for h, w in headers:
                self.cell(w, 8, h, 1, 0, 'C', True)
            self.ln()

            self.set_font('Helvetica', '', 10)
            self.set_text_color(0)
            
            for row in table_data:
                self.cell(20, 7, str(row['layer']), 1, 0, 'C')
                self.cell(35, 7, f"{row['area']:.2f}", 1, 0, 'C')
                self.cell(35, 7, f"{row['pressure']:.2f}", 1, 0, 'C')
                self.cell(30, 7, f"{row['force_kn']:.3f}", 1, 0, 'C')
                self.cell(30, 7, f"{row['pct_inc']:.1f}%", 1, 1, 'C')
                
                if self.get_y() > 270:
                    self.add_page()
                    self.set_font('Helvetica', 'B', 10)
                    self.set_fill_color(0, 51, 102); self.set_text_color(255)
                    for h, w in headers:
                        self.cell(w, 8, h, 1, 0, 'C', True)
                    self.ln()
                    self.set_font('Helvetica', '', 10); self.set_text_color(0)

        def add_comparison_table(self, waam_data, pm_data):
            self.set_font('Helvetica', 'B', 10)
            self.set_fill_color(0, 51, 102); self.set_text_color(255)
            
            self.cell(60, 8, "Metric", 1, 0, 'C', True)
            self.cell(60, 8, "WAAM (Fusion)", 1, 0, 'C', True)
            self.cell(60, 8, "PM (Solid State)", 1, 1, 'C', True)
            self.ln()
            
            self.set_font('Helvetica', '', 10); self.set_text_color(0)
            
            metrics = [
                ("Total Layers", waam_data['layers'], pm_data['layers']),
                ("Deposition Rate (cm3/hr)", f"{waam_data['dep_rate']:.1f}", f"{pm_data['dep_rate']:.1f}"),
                ("Specific Energy (J/mm3)", f"{waam_data['spec_energy']:.1f}", f"{pm_data['spec_energy']:.1f}"),
                ("Total Energy (kJ)", f"{waam_data['total_energy']:.1f}", f"{pm_data['total_energy']:.1f}"),
                ("Process Time (min)", f"{waam_data['time']:.1f}", f"{pm_data['time']:.1f}"),
                ("Peak Force (kN)", "0.0", f"{pm_data['force']:.3f}")
            ]
            
            for m, w, p in metrics:
                self.cell(60, 7, m, 1, 0, 'L')
                self.cell(60, 7, str(w), 1, 0, 'C')
                self.cell(60, 7, str(p), 1, 1, 'C')
                self.ln()

else:
    class PDFReport:
        def __init__(self, process_name=""):
            raise ImportError("FPDF library not found. Please install it with 'pip install fpdf'")

class ReportGenerator:
    @staticmethod
    def generate_waam(inputs, results, filepath):
        pdf = PDFReport("WAAM PROCESS")
        pdf.add_page()
        
        # Time is now in header, removing redundant line
        pdf.section_title("1. GLOBAL PARAMETERS")
        pdf.add_row("Target Layers (N)", results.total_layers, "") 
        pdf.add_row("Total Height", inputs['target_h'], "mm")
        pdf.add_row("Current", inputs['amps'], "A")
        pdf.add_row("Voltage", inputs['volts'], "V")
        pdf.add_row("Travel Speed", inputs['speed'], "mm/min")
        pdf.add_row("Wire Diameter", inputs['wire_d'], "mm")
        pdf.add_row("Target Bead Width", inputs['bead_width'], "mm") 
        pdf.add_row("Layer Height", inputs['layer_h'], "mm")
        pdf.add_row("Bead Length", inputs['bead_len'], "mm")
        pdf.ln(5)

        pdf.section_title("2. ENERGY & OUTPUTS")
        pdf.add_row("Total Input Power", f"{results.total_power:.1f}", "W")
        pdf.add_row("Effective Power (Peff)", f"{results.power_effective:.1f}", "W")
        pdf.add_row("Linear Energy Density (El)", format_energy(results.linear_energy, per_mm=True), "")
        pdf.add_row("Deposition Rate", f"{results.deposition_rate_kg_h:.3f}", "kg/h")
        pdf.add_row("Calculated WFS", f"{results.wire_feed_speed:.3f}", "m/min")
        pdf.add_row("Single Layer Energy", format_energy(results.single_layer_energy), "")
        pdf.add_row("Total Energy (N layers)", format_energy(results.totalenergyfornlayers), "")
        pdf.add_row("Total Deposition Mass", f"{results.total_mass_kg*1000:.1f}", "g")
        pdf.add_row("Total Build Time", f"{results.total_time_min:.1f}", "min")
        pdf.add_row("Heat Warning", ("YES" if results.heat_warning else "NO"), "")
        pdf.ln(5)

        pdf.add_page()
        pdf.section_title(f"3. LAYER DEPOSITION SCHEDULE (N={results.total_layers})")
        pdf.ln(2)
        pdf.add_layer_table(results.layer_data)

        pdf.output(filepath)
        return filepath

    @staticmethod
    def generate_pm(inputs, results, filepath):
        pdf = PDFReport("PM CONSOLIDATION")
        pdf.add_page()
        
        pdf.section_title("1. PM CONSOLIDATION INPUTS")
        pdf.add_row("Length L", inputs['L'], "mm")
        pdf.add_row("Width W", inputs['W'], "mm")
        pdf.add_row("Target Height", inputs['H'], "mm")
        pdf.add_row("Alpha", inputs['Alpha'], "")
        pdf.add_row("Beta", inputs['Beta'], "")
        pdf.add_row("Base Pressure", inputs['Pressure'], "MPa")
        pdf.ln(5)

        pdf.section_title("2. RESULTS SUMMARY")
        pdf.add_row("Max Peak Force", f"{results['max_force_kn']:.3f}", "kN")
        pdf.add_row("Total Load", f"{results['total_force_kn']:.3f}", "kN")
        pdf.add_row("Cumulative Work", f"{results['total_work_kj']:.3f}", "kJ")
        pdf.add_row("Stroke Length", f"{results['stroke_mm']:.2f}", "mm")
        pdf.ln(5)
        
        pdf.add_page()
        pdf.section_title(f"3. LAYER DATA (N={len(results['table'])})")
        pdf.ln(2)
        pdf.add_pm_table(results['table'])
        
        pdf.output(filepath)
        return filepath
    
    @staticmethod
    def generate_comparison(inputs, w, p, filepath):
        pdf = PDFReport("COMPARATIVE ANALYSIS")
        pdf.add_page()
        pdf.section_title("COMPARISON REPORT")
        pdf.add_row("Target Vol", f"{float(inputs['L'])*float(inputs['W'])*float(inputs['H']):.0f}", "mm3")
        pdf.ln(5)
        pdf.add_comparison_table(w, p)
        
        pdf.ln(10)
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(0, 10, "Detailed Analysis", 0, 1)
        pdf.set_font('Helvetica', '', 10)
        
        pm_time = p['time'] if p['time'] > 0 else 1e-9
        waam_energy = w['total_energy'] if w['total_energy'] > 0 else 1e-9
        
        ratio_time = w['time'] / pm_time
        ratio_energy = p['total_energy'] / waam_energy
        
        vol = float(inputs['L'])*float(inputs['W'])*float(inputs['H'])
        waam_eff = (w['total_energy'] * 1000) / vol if vol > 0 else 0
        pm_eff = (p['total_energy'] * 1000) / vol if vol > 0 else 0

        analysis = (
            f"1. PROCESS SPEED & PRODUCTIVITY:\n"
            f"   - WAAM Time: {w['time']:.1f} min | PM Time: {p['time']:.1f} min\n"
            f"   - Speed Factor: PM is {ratio_time:.2f}x faster/slower than WAAM.\n"
            f"   - Deposition Rate: WAAM ({w['dep_rate']:.1f} cm3/h) vs PM ({p['dep_rate']:.1f} cm3/h).\n\n"
            
            f"2. ENERGY EFFICIENCY:\n"
            f"   - WAAM Energy: {w['total_energy']:.1f} kJ | PM Work: {p['total_energy']:.1f} kJ\n"
            f"   - Consumption Ratio: PM uses {ratio_energy*100:.1f}% of the energy required by WAAM.\n"
            f"   - Specific Energy: WAAM requires {waam_eff:.2f} J/mm3 vs PM {pm_eff:.2f} J/mm3.\n\n"
            
            f"3. MECHANICAL & FORCE REQUIREMENTS:\n"
            f"   - WAAM: Non-contact fusion process (0 kN force). Requires thermal management.\n"
            f"   - PM: Solid-state compaction requiring {p['force']:.1f} kN peak force.\n\n"
            
            f"4. FINAL RECOMMENDATION:\n"
            f"   - Select WAAM for complex, freeform geometries where tool access is needed.\n"
            f"   - Select PM Consolidation for simple blocks where speed and energy efficiency are critical.\n"
        )
        pdf.multi_cell(0, 6, analysis)
        pdf.output(filepath)



class ComparisonPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True)
        self.scroll_frame.columnconfigure(0, weight=1, minsize=480)
        self.scroll_frame.columnconfigure(1, weight=1, minsize=480)

        # LEFT: Config
        left = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=24, pady=10)
        
        ctrl = GlassCard(left, title="🏗️ FABRICATION SETUP", color=COLOR_NEON_ORANGE)
        ctrl.pack(fill="x")
        
        self.add_label(ctrl, "TARGET BLOCK (mm)")
        f_geo = ctk.CTkFrame(ctrl, fg_color="transparent"); f_geo.pack(fill="x", padx=20)
        self.ent_L = self.mini_input(f_geo, "L", "100")
        self.ent_W = self.mini_input(f_geo, "W", "50")
        self.ent_H = self.mini_input(f_geo, "H", "20")

        # WAAM Inputs (Updated to match WAAMPanel logic)
        ctk.CTkFrame(ctrl, height=1, fg_color=COLOR_BORDER_LIGHT).pack(fill="x", padx=20, pady=5)
        self.add_label(ctrl, "PROCESS A: WAAM (Arc + Wire)")
        self.ent_w_v = self.add_input_row(ctrl, "Voltage (V)", "22.0")
        self.ent_w_i = self.add_input_row(ctrl, "Current (A)", "180")
        self.ent_w_eff = self.add_input_row(ctrl, "Efficiency", "0.8")
        
        f_w = ctk.CTkFrame(ctrl, fg_color="transparent"); f_w.pack(fill="x", padx=20, pady=2)
        # Added Travel Speed here as requested
        self.ent_w_wired = self.mini_input(f_w, "Wire Dia (mm)", "1.2")
        self.ent_w_wfs = self.mini_input(f_w, "WFS (m/min)", "5.0")
        self.ent_w_speed = self.mini_input(f_w, "Speed (mm/min)", "300") # New Input
        self.ent_w_lh = self.mini_input(f_w, "Layer H (mm)", "2.0") # Added Layer Height input

        # PM Inputs (Updated to match PMPanel logic)
        ctk.CTkFrame(ctrl, height=1, fg_color=COLOR_BORDER_LIGHT).pack(fill="x", padx=20, pady=5)
        self.add_label(ctrl, "PROCESS B: PM CONSOLIDATION")
        self.ent_p_alpha = self.add_input_row(ctrl, "Alpha", "0.03")
        self.ent_p_beta = self.add_input_row(ctrl, "Beta", "0.05")
        self.ent_p_press = self.add_input_row(ctrl, "Pressure (MPa)", "300")
        self.ent_p_cycle = self.add_input_row(ctrl, "Cycle (s)", "15")
        self.ent_p_cr = self.add_input_row(ctrl, "Comp. Ratio", "2.0") # Added CR
        
        f_p = ctk.CTkFrame(ctrl, fg_color="transparent"); f_p.pack(fill="x", padx=20)
        self.ent_p_hdep = self.mini_input(f_p, "H_dep (mm)", "2.0")
        self.ent_p_hmach = self.mini_input(f_p, "H_mach (mm)", "0.5")

        ctk.CTkButton(left, text="▶ RUN SIMULATION", height=50, command=self.compare, fg_color=COLOR_NEON_ORANGE).pack(fill="x", pady=20)

        # RIGHT: Viz
        right = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=24, pady=10)
        
        graph_card = GlassCard(right, title="📊 COMPARISON METRICS", color=COLOR_NEON_BLUE)
        graph_card.pack(fill="both", pady=(0,10))
        
        # 2 Subplots (Energy & Force) - Removed Time
        self.fig, (self.ax1, self.ax2) = plt.subplots(1, 2, facecolor=COLOR_BG_PRIMARY, dpi=80)
        self.fig.set_size_inches(12, 3.5)
        self.fig.subplots_adjust(wspace=0.4, bottom=0.15)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_card)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

        report_card = GlassCard(right, title="📝 ANALYSIS REPORT", color=COLOR_NEON_GREEN)
        report_card.pack(fill="both", expand=True)
        self.lbl_rep = ctk.CTkLabel(report_card, text="Ready to simulate...", font=("Consolas", 11), justify="left", anchor="nw")
        self.lbl_rep.pack(fill="both", expand=True, padx=10, pady=10)
        
        ctk.CTkButton(right, text="📄 GENERATE PDF", command=self.gen_report, fg_color=COLOR_BUTTON_SUCCESS).pack(fill="x", pady=10)

    def add_label(self, p, t): ctk.CTkLabel(p, text=t, font=("Arial",12,"bold"), text_color="gray").pack(anchor="w", padx=20, pady=2)
    
    def add_input_row(self, p, l, d):
        f = ctk.CTkFrame(p, fg_color="transparent"); f.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(f, text=l, font=("Arial", 12)).pack(side="left")
        e = ctk.CTkEntry(f, width=100); e.pack(side="right"); e.insert(0, d)
        return e
        
    def mini_input(self, p, l, d):
        # Helper to pack small inputs side-by-side
        f_inner = ctk.CTkFrame(p, fg_color="transparent")
        f_inner.pack(side="left", expand=True)
        ctk.CTkLabel(f_inner, text=l, font=("Arial", 11)).pack(side="left", padx=2)
        e = ctk.CTkEntry(f_inner, width=60); e.pack(side="left"); e.insert(0, d)
        return e

    def compare(self):
        try:
            # 1. Target Geometry
            L, W, H = float(self.ent_L.get()), float(self.ent_W.get()), float(self.ent_H.get())
            vol_target_mm3 = L * W * H
            
            # --- WAAM CALCULATION (Energy = Peff / Speed) ---
            V = float(self.ent_w_v.get())
            I = float(self.ent_w_i.get())
            eff = float(self.ent_w_eff.get())
            wire_d = float(self.ent_w_wired.get())
            wfs_m_min = float(self.ent_w_wfs.get())
            speed_mm_min = float(self.ent_w_speed.get())
            layer_h = float(self.ent_w_lh.get())
            
            # 1. Effective Power
            P_eff = V * I * eff # Watts
            
            # 2. Linear Energy (J/mm)
            speed_mm_s = speed_mm_min / 60.0
            E_linear = P_eff / speed_mm_s if speed_mm_s > 0 else 0
            
            # 3. Deposition & Path Logic
            # Volumetric Rate (mm3/min)
            A_wire = math.pi * ((wire_d / 2)**2)
            vol_rate_mm3_min = A_wire * (wfs_m_min * 1000.0)
            
            # 4. Total Energy (kJ) using simplified formula: Energy = Linear_E * L * H / Layer_H
            if layer_h > 0:
                energy_w_J = E_linear * L * H / layer_h
            else:
                energy_w_J = 0
            
            energy_w_kJ = energy_w_J / 1000.0
            
            # Deposition Rate for Comparison (cm3/h)
            dep_rate_w_cm3_h = (vol_rate_mm3_min * 60) / 1000.0
            
            # Time (min)
            if vol_rate_mm3_min > 0:
                time_w_min = vol_target_mm3 / vol_rate_mm3_min
            else:
                time_w_min = 0


            # --- PM CALCULATION (Matches PM Panel Logic) ---
            alpha, beta = float(self.ent_p_alpha.get()), float(self.ent_p_beta.get())
            Press, cycle = float(self.ent_p_press.get()), float(self.ent_p_cycle.get())
            h_d, h_m = float(self.ent_p_hdep.get()), float(self.ent_p_hmach.get())
            CR = float(self.ent_p_cr.get())
            
            h_eff = h_d - h_m
            layers_p = math.ceil(H / h_eff) if h_eff > 0 else 0
            
            # Stroke Logic
            stroke_mm = h_d * (CR - 1)
            
            # Loop for total work and max force
            A_n = L * W # Initial Area
            P_n = Press # Initial Pressure
            
            forces = []
            work_J_list = []
            
            for _ in range(layers_p):
                F_n = A_n * P_n # Newtons
                forces.append(F_n)
                
                # Work = Force * Distance
                w_j = F_n * (stroke_mm / 1000.0)
                work_J_list.append(w_j)
                
                # Update for next layer (Consolidation Physics)
                A_n *= (1 - beta)
                P_n *= (1 + alpha)
            
            max_F_kN = (max(forces) / 1000.0) if forces else 0
            energy_p_kJ = sum(work_J_list) / 1000.0
            time_p_min = (layers_p * cycle) / 60.0
            
            dep_rate_p_cm3_h = (vol_target_mm3 / 1000.0) / (time_p_min / 60.0) if time_p_min > 0 else 0

            # --- PLOTTING ---
            self.ax1.clear(); self.ax2.clear()
            
            labels = ['WAAM', 'PM']
            cols = [COLOR_NEON_RED, COLOR_NEON_GREEN]
            
            # 1. Energy
            self.ax1.bar(labels, [energy_w_kJ, energy_p_kJ], color=cols, alpha=0.8)
            self.ax1.set_title('Total Energy (kJ)', fontsize=10, fontweight='bold')
            self.ax1.grid(axis='y', alpha=0.3)
            
            # 2. Force
            self.ax2.bar(labels, [0, max_F_kN], color=cols, alpha=0.8)
            self.ax2.set_title('Peak Force (kN)', fontsize=10, fontweight='bold')
            self.ax2.grid(axis='y', alpha=0.3)
            
            self.canvas.draw()

            # --- TEXT REPORT ---
            # WAAM Layers approximation for display (depends on arbitrary layer height, say 2mm default)
            waam_approx_layers = int(H / 2.0) 
            
            txt = f"COMPARATIVE ANALYSIS ({int(L)}x{int(W)}x{int(H)} mm)\n{'-'*45}\n"
            txt += f"{'METRIC':<20} | {'WAAM':<10} | {'PM':<10}\n"
            txt += f"{'-'*45}\n"
            txt += f"{'Energy (kJ)':<20} | {energy_w_kJ:<10.1f} | {energy_p_kJ:<10.1f}\n"
            txt += f"{'Peak Force (kN)':<20} | {0:<10} | {max_F_kN:<10.1f}\n"
            txt += f"{'-'*45}\n"
            txt += f"WAAM Power: {int(P_eff)} W (Eff)\n"
            txt += f"PM Stroke: {stroke_mm:.2f} mm per layer\n"
            
            self.lbl_rep.configure(text=txt)

            # Store for PDF
            self.last_res = {
                'waam': {
                    'layers': waam_approx_layers, # Approx
                    'dep_rate': dep_rate_w_cm3_h,
                    'spec_energy': (energy_w_kJ*1000)/vol_target_mm3 if vol_target_mm3 else 0,
                    'total_energy': energy_w_kJ,
                    'time': time_w_min,
                    'force': 0
                },
                'pm': {
                    'layers': layers_p,
                    'dep_rate': dep_rate_p_cm3_h,
                    'spec_energy': (energy_p_kJ*1000)/vol_target_mm3 if vol_target_mm3 else 0,
                    'total_energy': energy_p_kJ,
                    'time': time_p_min,
                    'force': max_F_kN
                }
            }

        except Exception as e:
            self.lbl_rep.configure(text=f"Error: {e}")
            messagebox.showerror("Simulation Error", str(e))

    def gen_report(self):
        if not hasattr(self, 'last_res'): return messagebox.showwarning("Wait", "Run Sim first")
        
        try:
            filename_ts = datetime.now().strftime('%Y%m%d_%H%M')
            initial_name = f"Comp_Report_{filename_ts}.pdf"
            filepath = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Documents", "*.pdf")], initialfile=initial_name)
            
            if filepath and FPDF:
                # 1. Save graph
                temp_graph = "temp_comp_plot.png"
                self.fig.savefig(temp_graph, dpi=150, bbox_inches='tight')
                
                # 2. Create PDF
                pdf = PDFReport()
                pdf.add_page()
                pdf.section_title("COMPARATIVE PROCESS REPORT")
                
                # 3. Geometry Inputs
                pdf.set_font("Arial", "", 10)
                pdf.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, 'R')
                pdf.ln(5)
                
                pdf.set_fill_color(240, 240, 240)
                pdf.cell(0, 8, "  Target Geometry", 0, 1, 'L', True)
                pdf.ln(2)
                
                L, W, H = self.ent_L.get(), self.ent_W.get(), self.ent_H.get()
                pdf.cell(30, 6, f"  Length: {L} mm", 0, 0)
                pdf.cell(30, 6, f"  Width: {W} mm", 0, 0)
                pdf.cell(30, 6, f"  Height: {H} mm", 0, 1)
                pdf.ln(5)

                # 3b. WAAM Parameters
                pdf.set_fill_color(240, 240, 240)
                pdf.cell(0, 8, "  WAAM Process Parameters", 0, 1, 'L', True)
                pdf.ln(2)
                
                pdf.add_row("Voltage", self.ent_w_v.get(), "V")
                pdf.add_row("Current", self.ent_w_i.get(), "A")
                pdf.add_row("Efficiency", self.ent_w_eff.get(), "")
                pdf.add_row("Wire Diameter", self.ent_w_wired.get(), "mm")
                pdf.add_row("Wire Feed Speed", self.ent_w_wfs.get(), "m/min")
                pdf.add_row("Travel Speed", self.ent_w_speed.get(), "mm/min")
                pdf.add_row("Layer Height", self.ent_w_lh.get(), "mm")
                pdf.ln(5)

                # 3c. PM Parameters
                pdf.set_fill_color(240, 240, 240)
                pdf.cell(0, 8, "  PM Process Parameters", 0, 1, 'L', True)
                pdf.ln(2)

                pdf.add_row("Alpha (Resistance)", self.ent_p_alpha.get(), "")
                pdf.add_row("Beta (Distortion)", self.ent_p_beta.get(), "")
                pdf.add_row("Base Pressure", self.ent_p_press.get(), "MPa")
                pdf.add_row("Cycle Time", self.ent_p_cycle.get(), "s")
                pdf.add_row("Compression Ratio", self.ent_p_cr.get(), "")
                pdf.add_row("Deposition Height", self.ent_p_hdep.get(), "mm")
                pdf.add_row("Machining Cut", self.ent_p_hmach.get(), "mm")
                pdf.ln(5)

                # 4. Comparison Table
                pdf.section_title("PROCESS PERFORMANCE METRICS")
                
                # Header
                pdf.set_font("Arial", "B", 10)
                pdf.set_fill_color(0, 102, 204); pdf.set_text_color(255)
                pdf.cell(60, 8, "Metric", 1, 0, 'C', True)
                pdf.cell(60, 8, "WAAM (Arc)", 1, 0, 'C', True)
                pdf.cell(60, 8, "PM (Solid State)", 1, 1, 'C', True)
                
                # Rows
                pdf.set_font("Arial", "", 10); pdf.set_text_color(0)
                
                waam = self.last_res['waam']
                pm = self.last_res['pm']
                
                metrics = [
                    ("Energy Input", f"{waam['total_energy']:.2f} kJ", f"{pm['total_energy']:.2f} kJ"),
                    ("Peak Force", "0.0 kN", f"{pm['force']:.2f} kN")
                ]
                
                for m, w_val, p_val in metrics:
                    pdf.cell(60, 7, m, 1, 0, 'L')
                    pdf.cell(60, 7, w_val, 1, 0, 'C')
                    pdf.cell(60, 7, p_val, 1, 1, 'C')
                    pdf.ln()
                
                pdf.ln(5)

                # 5. Insert Graph
                if os.path.exists(temp_graph):
                    pdf.set_font("Arial", "B", 11)
                    pdf.cell(0, 8, "Visual Comparison", 0, 1, 'L')
                    pdf.image(temp_graph, x=10, w=190)
                    pdf.ln(5)
                    os.remove(temp_graph)

                # 6. Conclusion
                pdf.set_font("Arial", "B", 11)
                pdf.cell(0, 8, "Summary", 0, 1, 'L')
                pdf.set_font("Arial", "", 10)
                
                # Dynamic summary based on process data
                waam_e = waam['total_energy']
                pm_e = pm['total_energy']
                pm_f = pm['force']
                
                txt = f"Comparative Analysis Summary:\n\n"
                
                if waam_e > pm_e:
                    diff = waam_e - pm_e
                    energy_ratio = waam_e / pm_e if pm_e > 0 else 0
                    txt += f"1. ENERGY: PM Consolidation is more energy-efficient for this geometry, saving {diff:.1f} kJ compared to WAAM. WAAM consumes approx. {energy_ratio:.1f}x more energy.\n\n"
                else:
                    diff = pm_e - waam_e
                    txt += f"1. ENERGY: WAAM is more energy-efficient in this scenario, saving {diff:.1f} kJ compared to PM.\n\n"
                
                txt += f"2. MECHANICS: PM Consolidation is a solid-state process involving high mechanical loads, requiring a peak force of {pm_f:.1f} kN. WAAM is a fusion-based process with negligible mechanical force (0 kN) but high thermal input."
                
                pdf.multi_cell(0, 5, txt)
                
                pdf.output(filepath)
                webbrowser.open('file://' + filepath)
                
            elif not FPDF:
                messagebox.showerror("Error", "FPDF library missing.")
                
        except Exception as e:
            messagebox.showerror("Report Error", str(e))


# 🖥️ UI: WAAM PANEL
class WAAMPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=COLOR_BG_PRIMARY, corner_radius=0)
        
        self.canvas = ctk.CTkCanvas(self, bg=COLOR_BG_PRIMARY, highlightthickness=0)
        scrollbar = ctk.CTkScrollbar(self, command=self.canvas.yview)
        self.scrollable_frame = ctk.CTkFrame(self.canvas, fg_color=COLOR_BG_PRIMARY)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self._canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._canvas_window, width=e.width))
        
        self.scrollable_frame.columnconfigure(0, weight=1)
        self.scrollable_frame.columnconfigure(1, weight=1)
        self.scrollable_frame.rowconfigure(0, weight=1)

        # LEFT COLUMN (Inputs)
        left = ctk.CTkFrame(self.scrollable_frame, fg_color=COLOR_BG_SECONDARY, corner_radius=6)
        left.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        left.columnconfigure(0, weight=1)
        left.columnconfigure(1, weight=1)
        
        self.inputs = {}
        # Changed: Removed WFS from Electrical, moved to Geometry Outputs
        self.add_section(left, "⚡ ELECTRICAL & WIRE", [
            ("Voltage (V)", "volts", "22.0"), 
            ("Current (A)", "amps", "180"),
            ("Efficiency (η)", "eff", "0.8"), 
        ])
        
        # Changed: Added Bead Width to Geometry Inputs
        self.add_section(left, "📐 GEOMETRY & PATH", [
            ("Bead Width (mm)", "bead_width", "6.0"), # <-- Input Swapped Here
            ("Travel Speed (mm/min)", "speed", "300"),
            ("Wire Diameter (mm)", "wire_d", "1.2"), 
            ("Target Height (mm)", "target_h", "10.0"),
            ("Layer Height (mm)", "layer_h", "2.0"), 
            ("Bead Length (mm)", "bead_len", "60.0"),
            ("Density (g/cm³)", "density", "7.85")
        ])

        # RIGHT COLUMN (Outputs)
        right = ctk.CTkFrame(self.scrollable_frame, fg_color=COLOR_BG_SECONDARY, corner_radius=6)
        right.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        right.columnconfigure(0, weight=1)
        for i in range(4): right.rowconfigure(i, weight=0)

        self.outputs = {}
        
        # Changed: Swapped Calc Width output for Calc WFS output
        self.add_card(right, "📏 GEOMETRY OUTPUT", 
                      ["Calc. WFS (m/min)", "Vol/Layer (mm³)", "Vol of wire/Layer(mm³)", "Total Layers"], 
                      ["lbl_wfs", "lbl_vol_layer", "lbl_vol_wire_per_layer", "lbl_layers"], 0)

        self.add_card(right, "🔥 ENERGY", 
                      ["Effective Arc Power (W)", "Energy (J/mm)", "Total Energy (kJ)"], 
                      ["lbl_power", "lbl_energy", "lbl_total_energy_n"], 1)

        self.add_card(right, "🏗️ PRODUCTION", 
                      ["Deposition Rate (kg/h)", "Total Mass (g)", "Total Time (min)"], 
                      ["lbl_rate", "lbl_mass", "lbl_time"], 2)

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=0, pady=(20, 0))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)
        
        self.calc = WAAMCalculator()
        self.last_res = None

        ctk.CTkButton(btns, text="▶ CALCULATE", height=45, fg_color=COLOR_BUTTON_PRIMARY, corner_radius=6,
                      hover_color="#0052a3", text_color="white", font=("Arial",13,"bold"),
                      command=self.run_calc).grid(row=0, column=0, sticky="ew", padx=4, pady=6)
                      
        ctk.CTkButton(btns, text="📄 REPORT", height=45, fg_color=COLOR_BUTTON_SUCCESS, corner_radius=6,
                      hover_color="#008800", text_color="white", font=("Arial",13,"bold"),
                      command=self.gen_pdf).grid(row=0, column=1, sticky="ew", padx=4, pady=6)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def add_section(self, parent, title, fields):
        row_idx = getattr(parent, "_row_idx", 0)
        
        header_frame = ctk.CTkFrame(parent, fg_color=COLOR_ACCENT_BLUE, corner_radius=4)
        header_frame.grid(row=row_idx, column=0, columnspan=2, sticky="ew", padx=6, pady=(10,6))
        
        ctk.CTkLabel(header_frame, text=title, font=("Arial",16,"bold"), text_color="white", 
                     fg_color="transparent").pack(anchor="w", padx=10, pady=6)
        row_idx += 1

        for lbl, key, val in fields:
            l = ctk.CTkLabel(parent, text=lbl, text_color=COLOR_TEXT_PRIMARY, font=("Arial",12))
            l.grid(row=row_idx, column=0, sticky="w", padx=10, pady=4)

            e = ctk.CTkEntry(parent, placeholder_text=val, width=140, justify="center", 
                             font=("Arial",12), fg_color="white", border_width=1, border_color=COLOR_BORDER_LIGHT)
            e.insert(0, val)
            e.grid(row=row_idx, column=1, sticky="ew", padx=10, pady=4)

            self.inputs[key] = e
            row_idx += 1
        
        parent._row_idx = row_idx

    def add_card(self, parent, title, labels, keys, row_num):
        card = ctk.CTkFrame(parent, fg_color=COLOR_BG_PRIMARY, border_width=1, border_color=COLOR_BORDER_LIGHT, corner_radius=4)
        card.grid(row=row_num, column=0, sticky="ew", pady=5, padx=0)
        card.columnconfigure(0, weight=1)
        
        header = ctk.CTkFrame(card, fg_color=COLOR_ACCENT_BLUE, corner_radius=3)
        header.pack(fill="x", padx=4, pady=4)
        
        ctk.CTkLabel(header, text=title, font=("Arial",13,"bold"), text_color="white", 
                     fg_color="transparent").pack(anchor="w", padx=8, pady=4)
        
        for l, k in zip(labels, keys):
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(r, text=l, text_color=COLOR_TEXT_SECONDARY, font=("Arial",11)).pack(side="left")
            self.outputs[k] = ctk.CTkLabel(r, text="--", font=("Arial",14,"bold"), text_color=COLOR_ACCENT_BLUE)
            self.outputs[k].pack(side="right")

    def run_calc(self):
        try:
            d = {k: float(v.get()) for k,v in self.inputs.items()}
            # Pass bead_width input to calculation
            res = self.calc.calculate(d['volts'], d['amps'], d['eff'], d['speed'], d['wire_d'], 
                                    d['layer_h'], d['target_h'], d['bead_len'], d['density'], d['bead_width'])
            self.last_res = res

            self.outputs['lbl_wfs'].configure(text=f"{res.wire_feed_speed:.2f}") # Updated to show WFS
            self.outputs['lbl_vol_layer'].configure(text=f"{res.vol_per_layer_mm3:.0f}")
            self.outputs['lbl_vol_wire_per_layer'].configure(text=f"{res.vol_wire_per_layer:.0f}")
            self.outputs['lbl_layers'].configure(text=str(res.total_layers))
            self.outputs['lbl_power'].configure(text=f"{int(res.power_effective)} W")
            self.outputs['lbl_energy'].configure(text=format_energy(res.linear_energy, per_mm=True))
            self.outputs['lbl_total_energy_n'].configure(text=format_energy(res.totalenergyfornlayers))
            self.outputs['lbl_rate'].configure(text=f"{res.deposition_rate_kg_h:.2f}")
            self.outputs['lbl_mass'].configure(text=f"{res.total_mass_kg*1000:.1f} g")
            self.outputs['lbl_time'].configure(text=f"{res.total_time_min:.1f} min")

        except ValueError:
            messagebox.showerror("Error", "Invalid Input. Check numeric fields.")

    def gen_pdf(self):
        if not self.last_res: return messagebox.showwarning("Wait", "Run Calculation First")
        try:
            filename_ts = datetime.now().strftime('%Y%m%d')
            initial_name = f"WAAM_Width_{self.inputs['bead_width'].get()}mm_{filename_ts}.pdf"
            
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF Documents", "*.pdf")],
                initialfile=initial_name
            )
            
            if path:
                d = {k: v.get() for k,v in self.inputs.items()}
                ReportGenerator.generate_waam(d, self.last_res, path)
                if messagebox.askyesno("Success", "Schedule Generated. Open?"): webbrowser.open(path)
        except Exception as e:
            messagebox.showerror("Error", str(e))


class ProHMI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.latest_version = None
        self.latest_installer_url = None
        self.latest_release_url = None

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1600x950")
        self.minsize(1000, 600)
        self.configure(fg_color=COLOR_BG_PRIMARY)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        # Add footer row config
        self.grid_rowconfigure(2, weight=0)

        self.top_bar = ctk.CTkFrame(self, corner_radius=0, fg_color="#ffffff", border_width=0)
        self.top_bar.grid(row=0, column=0, sticky="ew")
        self.top_bar.grid_propagate(False)
        self.top_bar.configure(height=95)

        header_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 5))

        title_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_frame.pack(side="left", expand=True)

        self.logo_img = None
        if os.path.exists(LOGO_PATH):
            try:
                self.logo_img = ctk.CTkImage(light_image=Image.open(LOGO_PATH), dark_image=Image.open(LOGO_PATH), size=(28, 28))
                ctk.CTkLabel(title_frame, image=self.logo_img, text="").pack(side="left", padx=(0, 6))
            except Exception:
                self.logo_img = None

        ctk.CTkLabel(title_frame, text="HAM LAB", font=("Arial", 16, "bold"), text_color=COLOR_TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(title_frame, text=" • ", font=("Arial", 12), text_color=COLOR_TEXT_SECONDARY).pack(side="left", padx=5)
        ctk.CTkLabel(title_frame, text=f"Advanced Engineering Physics ({APP_VERSION})", font=("Arial", 11), text_color=COLOR_TEXT_SECONDARY).pack(side="left")

        self.status_indicator = ctk.CTkLabel(header_frame, text="🟢 READY", font=("Arial", 10, "bold"), text_color=COLOR_ACCENT_GREEN)
        self.status_indicator.pack(side="right", padx=10)

        nav_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        nav_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(5, 10))

        buttons = [
            ("Home", self.show_home, "#0066cc"),
            ("FSW", self.show_fsw, "#5555ff"),
            ("WAAM", self.show_waam, "#5555ff"),
            ("PM Consolidation", self.show_pm, "#5555ff"),
            ("Compare", self.show_compare, "#5555ff"),
            ("Documentation", self.show_docs, "#5555ff"),
        ]
        
        for txt, cmd, color in buttons:
            ctk.CTkButton(nav_frame, text=txt, width=90, height=28, font=("Arial", 9, "bold"),
                          fg_color=color, text_color="white", corner_radius=5, command=cmd).pack(side="left", padx=3)

        self.btn_install_update = ctk.CTkButton(
            nav_frame,
            text="Install Update",
            width=110,
            height=28,
            font=("Arial", 9, "bold"),
            fg_color=COLOR_BUTTON_SUCCESS,
            hover_color="#008800",
            text_color="white",
            corner_radius=5,
            state="disabled",
            command=self.install_update,
        )
        self.btn_install_update.pack(side="right", padx=3)

        self.lbl_update_status = ctk.CTkLabel(
            nav_frame,
            text="Checking updates...",
            font=("Arial", 9, "bold"),
            text_color=COLOR_TEXT_SECONDARY,
        )
        self.lbl_update_status.pack(side="right", padx=8)

        self.main_container = ctk.CTkFrame(self, fg_color=COLOR_BG_PRIMARY)
        self.main_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        self.main_container.grid_propagate(True)

        self.frame_fsw = FSWPanel(self.main_container)
        self.frame_pm = PMConsolidationPanel(self.main_container)
        self.frame_waam = WAAMPanel(self.main_container)
        self.frame_compare = ComparisonPanel(self.main_container)
        self.frame_docs = DocumentationPanel(self.main_container)

        nav_callbacks = {
            "fsw": self.show_fsw,
            "pm": self.show_pm,
            "waam": self.show_waam,
            "compare": self.show_compare,
            "docs": self.show_docs,
        }
        self.frame_home = HomePanel(self.main_container, nav_callbacks=nav_callbacks)

        self.footer = ctk.CTkFrame(self, fg_color="transparent", height=30)
        self.footer.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self.copyright_label = ctk.CTkLabel(self.footer, text="© 2026 HAM Lab Engineering. All Rights Reserved.", 
                                            font=("Arial", 10), text_color="gray")
        self.copyright_label.pack(side="bottom", pady=2)

        self.show_home()
        self.after(1500, self.check_for_updates_async)

    def _normalize_version(self, raw):
        cleaned = str(raw).strip().lower().replace("v", "")
        pieces = []
        for token in re.split(r"[.-]", cleaned):
            match = re.match(r"^(\d+)", token)
            if match:
                pieces.append(int(match.group(1)))
            else:
                break
        return tuple(pieces) if pieces else (0,)

    def _is_newer_version(self, candidate, current):
        a = list(self._normalize_version(candidate))
        b = list(self._normalize_version(current))
        max_len = max(len(a), len(b))
        a.extend([0] * (max_len - len(a)))
        b.extend([0] * (max_len - len(b)))
        return tuple(a) > tuple(b)

    def check_for_updates_async(self):
        self.lbl_update_status.configure(text="Checking updates...", text_color=COLOR_TEXT_SECONDARY)
        self.btn_install_update.configure(state="disabled")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        try:
            req = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "HAMLab-Controller-Updater",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))

            latest_tag = payload.get("tag_name", "")
            release_url = payload.get("html_url")
            installer_url = None

            for asset in payload.get("assets", []):
                name = asset.get("name", "").lower()
                if name.endswith(".exe") and ("setup" in name or "installer" in name):
                    installer_url = asset.get("browser_download_url")
                    break

            if latest_tag and installer_url and self._is_newer_version(latest_tag, APP_VERSION):
                self.after(0, lambda: self._on_update_available(latest_tag, installer_url, release_url))
            else:
                self.after(0, self._on_up_to_date)

        except Exception:
            self.after(0, self._on_update_check_failed)

    def _on_update_available(self, latest_tag, installer_url, release_url):
        self.latest_version = latest_tag
        self.latest_installer_url = installer_url
        self.latest_release_url = release_url
        self.lbl_update_status.configure(text=f"Update: {latest_tag} available", text_color=COLOR_ACCENT_ORANGE)
        self.btn_install_update.configure(state="normal")

    def _on_up_to_date(self):
        self.latest_version = None
        self.latest_installer_url = None
        self.lbl_update_status.configure(text="Up to date", text_color=COLOR_ACCENT_GREEN)
        self.btn_install_update.configure(state="disabled")

    def _on_update_check_failed(self):
        self.lbl_update_status.configure(text="Update check failed", text_color=COLOR_ACCENT_RED)
        self.btn_install_update.configure(state="disabled")

    def install_update(self):
        if not self.latest_installer_url:
            messagebox.showinfo("No Update", "No installer URL found in latest GitHub release.")
            if self.latest_release_url:
                webbrowser.open(self.latest_release_url)
            return

        if not messagebox.askyesno("Install Update", f"Download and install {self.latest_version}?\n\nThe installer will run after download."):
            return

        self.btn_install_update.configure(state="disabled", text="Downloading...")
        self.lbl_update_status.configure(text="Downloading update...", text_color=COLOR_TEXT_SECONDARY)
        threading.Thread(target=self._download_update_worker, daemon=True).start()

    def _download_update_worker(self):
        try:
            updates_dir = os.path.join(tempfile.gettempdir(), "hamlab_updates")
            os.makedirs(updates_dir, exist_ok=True)
            file_name = f"HAMLab_Setup_{str(self.latest_version).replace('/', '_')}.exe"
            installer_path = os.path.join(updates_dir, file_name)

            urllib.request.urlretrieve(self.latest_installer_url, installer_path)
            self.after(0, lambda: self._on_update_downloaded(installer_path))
        except Exception as e:
            self.after(0, lambda: self._on_update_download_failed(str(e)))

    def _on_update_downloaded(self, installer_path):
        self.btn_install_update.configure(state="normal", text="Install Update")
        self.lbl_update_status.configure(text="Update ready to install", text_color=COLOR_ACCENT_GREEN)

        if messagebox.askyesno("Run Installer", "Update downloaded successfully.\n\nRun installer now?"):
            try:
                os.startfile(installer_path)
                self.after(600, self.destroy)
            except Exception as e:
                messagebox.showerror("Installer Launch Error", str(e))

    def _on_update_download_failed(self, error_text):
        self.btn_install_update.configure(state="normal", text="Install Update")
        self.lbl_update_status.configure(text="Update download failed", text_color=COLOR_ACCENT_RED)
        messagebox.showerror("Update Error", f"Could not download installer.\n\n{error_text}")

    def show_home(self): 
        self.select_frame(self.frame_home)
        self.status_indicator.configure(text="🟢 HOME")
        
    def show_fsw(self): 
        self.select_frame(self.frame_fsw)
        self.status_indicator.configure(text="⚙️ FSW")
        
    def show_pm(self):
        self.select_frame(self.frame_pm)
        self.status_indicator.configure(text="🧪 PM Consolidation")
        
    def show_waam(self): 
        self.select_frame(self.frame_waam)
        self.status_indicator.configure(text="⚡ WAAM")
        
    def show_compare(self): 
        self.select_frame(self.frame_compare)
        self.status_indicator.configure(text="🆚 Compare")

    def show_docs(self):
        self.select_frame(self.frame_docs)
        self.status_indicator.configure(text="📚 Documentation")

    def select_frame(self, frame):
        for widget in self.main_container.winfo_children():
            widget.pack_forget()
        frame.pack(fill="both", expand=True)

if __name__ == "__main__":
    app = ProHMI()
    app.mainloop()