from PIL import Image, ImageDraw
import math

# Create a larger icon with better visibility
size = 512
img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
draw = ImageDraw.Draw(img)

# Colors
gear_color = '#2196F3'  # Blue
arrow_color = '#FF6B6B'  # Red
outline = '#000000'

center_x, center_y = size // 2, size // 2

# Draw outer circle background
draw.ellipse([40, 40, size-40, size-40], fill='#E3F2FD', outline=outline, width=4)

# Draw gear (center)
gear_outer = 120
gear_inner = 70
draw.ellipse([center_x - gear_outer, center_y - gear_outer, 
              center_x + gear_outer, center_y + gear_outer], 
             fill=gear_color, outline=outline, width=3)
draw.ellipse([center_x - gear_inner, center_y - gear_inner, 
              center_x + gear_inner, center_y + gear_inner], 
             fill='#FFFFFF', outline=outline, width=2)

# Draw gear teeth
for angle in range(0, 360, 30):
    rad = math.radians(angle)
    x1 = center_x + 110 * math.cos(rad)
    y1 = center_y + 110 * math.sin(rad)
    x2 = center_x + 140 * math.cos(rad)
    y2 = center_y + 140 * math.sin(rad)
    draw.line([(x1, y1), (x2, y2)], fill=outline, width=4)

# Draw arrows around the circle (rotation indicator)
arrow_size = 30
# Top arrow
draw.polygon([
    (center_x, center_y - 180),
    (center_x + arrow_size, center_y - 150),
    (center_x - arrow_size, center_y - 150)
], fill=arrow_color)

# Right arrow
draw.polygon([
    (center_x + 180, center_y),
    (center_x + 150, center_y + arrow_size),
    (center_x + 150, center_y - arrow_size)
], fill=arrow_color)

# Bottom arrow
draw.polygon([
    (center_x, center_y + 180),
    (center_x - arrow_size, center_y + 150),
    (center_x + arrow_size, center_y + 150)
], fill=arrow_color)

# Left arrow
draw.polygon([
    (center_x - 180, center_y),
    (center_x - 150, center_y - arrow_size),
    (center_x - 150, center_y + arrow_size)
], fill=arrow_color)

# Save as ico with multiple resolutions
img.save('resources/thermal_bench.ico', format='ICO', 
         sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256)])
print("High-quality icon created: resources/thermal_bench.ico")
