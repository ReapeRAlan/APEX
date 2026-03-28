import numpy as np
from pathlib import Path

class Tiler:
    def __init__(self, patch_size=256, overlap=32):
        self.patch_size = patch_size
        self.stride = patch_size - overlap
        
    def create_patches(self, image_tensor: np.ndarray):
        # image_tensor: [C, H, W]
        C, H, W = image_tensor.shape
        patches = []
        coords = []
        
        for y in range(0, H - self.patch_size + 1, self.stride):
            for x in range(0, W - self.patch_size + 1, self.stride):
                patch = image_tensor[:, y:y+self.patch_size, x:x+self.patch_size]
                patches.append(patch)
                coords.append((y, x))
                
        # Handle edges si sobra espacio (opcional, o re-pad el original)
        
        return np.array(patches), coords
