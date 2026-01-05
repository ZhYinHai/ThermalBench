from PIL import Image, ImageDraw
import math

size = 256
img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
draw = ImageDraw.Draw(img)

# Draw outer circle
draw.ellipse([20, 20, 236, 236], outline='#000000', width=3)

# Draw gear (center)
draw.ellipse([80, 80, 176, 176], outline='#000000', width=3)
draw.ellipse([100, 100, 156, 156], fill='#FFFFFF', outline='#000000', width=2)

# Draw gear teeth
for angle in range(0, 360, 45):
    rad = math.radians(angle)
    x1 = 128 + 50 * math.cos(rad)
    y1 = 128 + 50 * math.sin(rad)
    x2 = 128 + 70 * math.cos(rad)
    y2 = 128 + 70 * math.sin(rad)
    draw.line([(x1, y1), (x2, y2)], fill='#000000', width=3)

# Draw arrows around circle
draw.polygon([(128, 40), (135, 55), (121, 55)], fill='#000000')  # Top
draw.polygon([(216, 128), (201, 135), (201, 121)], fill='#000000')  # Right

# Save as ico
img.save('resources/thermal_bench.ico', format='ICO', sizes=[(256, 256)])
print("Icon created: resources/thermal_bench.ico")
