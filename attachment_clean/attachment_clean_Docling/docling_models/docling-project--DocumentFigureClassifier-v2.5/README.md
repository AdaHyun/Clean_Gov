---
license: mit
base_model:
- google/efficientnet-b0
tags:
- image-classification
- document-analysis
- figure-classification
---


# EfficientNet-B0 Document Figure Classifier v2.5

This is an image classification model based on **Google EfficientNet-B0**, fine-tuned on a subset of the subset of HuggingFace/finepdfs to classify document figures into one of the following 26 categories:

1. **logo**
2. **photograph**
3. **icon**
4. **engineering_drawing**
5. **line_chart**
6. **bar_chart**
7. **other**
8. **table**
9. **flow_chart**
10. **screenshot_from_computer**
11. **signature**
12. **screenshot_from_manual**
13. **geographical_map**
14. **pie_chart**
15. **page_thumbnail**
16. **stamp**
17. **music**
18. **calendar**
19. **qr_code**
20. **bar_code**
21. **full_page_image**
22. **scatter_plot**
23. **chemistry_structure**
24. **topographical_map**
25. **crossword_puzzle**
26. **box_plot**


## Model Performance

**Note:** This model uses the same architecture and implementation as v2.0. The improved performance is achieved by training on a dataset that is 10 times larger than the one used for v2.0.

The model was evaluated on a held-out test set from the finepdfs dataset with the following metrics:

| Metric | v2.5 | v2.0 | Improvement |
|--------|------|------|-------------|
| **Accuracy** | 0.90703 | 0.87053 | +3.65% |
| **Balanced Accuracy** | 0.68836 | 0.60231 | +8.61% |
| **Macro F1** | 0.68942 | 0.60144 | +8.80% |
| **Weighted F1** | 0.90716 | 0.87270 | +3.45% |
| **Cohen's Kappa** | 0.87449 | 0.82563 | +4.89% |

### Per-Label Performance

| Label | Precision (v2.5) | Recall (v2.5) | Precision (v2.0) | Recall (v2.0) |
|-------|------------------|---------------|------------------|---------------|
| **logo** | 0.92807 | 0.91816 | 0.88317 | 0.88728 |
| **photograph** | 0.90966 | 0.96029 | 0.88169 | 0.93359 |
| **icon** | 0.83605 | 0.82678 | 0.79281 | 0.72133 |
| **engineering_drawing** | 0.71689 | 0.81172 | 0.58795 | 0.71555 |
| **line_chart** | 0.73055 | 0.92117 | 0.75865 | 0.84576 |
| **bar_chart** | 0.88599 | 0.92720 | 0.72624 | 0.93883 |
| **other** | 0.41893 | 0.38213 | 0.28239 | 0.37312 |
| **table** | 0.98636 | 0.96765 | 0.97950 | 0.95250 |
| **flow_chart** | 0.75926 | 0.82425 | 0.61527 | 0.81518 |
| **screenshot_from_computer** | 0.85952 | 0.71980 | 0.80510 | 0.65844 |
| **signature** | 0.89020 | 0.85971 | 0.91852 | 0.80914 |
| **screenshot_from_manual** | 0.48559 | 0.34543 | 0.34748 | 0.20662 |
| **geographical_map** | 0.86780 | 0.85219 | 0.82959 | 0.80720 |
| **pie_chart** | 0.96880 | 0.94220 | 0.89903 | 0.93931 |
| **page_thumbnail** | 0.52008 | 0.35188 | 0.40194 | 0.21475 |
| **stamp** | 0.71269 | 0.41794 | 0.63492 | 0.26258 |
| **music** | 0.48037 | 0.57778 | 0.76955 | 0.51944 |
| **calendar** | 0.52880 | 0.28775 | 0.51176 | 0.24786 |
| **qr_code** | 0.95694 | 0.93240 | 0.97500 | 0.90909 |
| **bar_code** | 0.34244 | 0.84305 | 0.12087 | 0.82063 |
| **full_page_image** | 0.40323 | 0.65789 | 0.43750 | 0.28116 |
| **scatter_plot** | 0.66848 | 0.67213 | 0.60386 | 0.68306 |
| **chemistry_structure** | 0.72781 | 0.65426 | 0.77444 | 0.54787 |
| **topographical_map** | 0.83333 | 0.38462 | 0.68750 | 0.28205 |
| **crossword_puzzle** | 0.57143 | 0.21622 | 0.80000 | 0.21622 |
| **box_plot** | 0.85714 | 0.64286 | 1.00000 | 0.07143 |


## How to use - Transformers

Example of how to classify an image into one of the 26 classes using transformers:

```python
import torch
import torchvision.transforms as transforms

from transformers import EfficientNetForImageClassification
from PIL import Image
import requests


urls = [
    'http://images.cocodataset.org/val2017/000000039769.jpg',
    'http://images.cocodataset.org/test-stuff2017/000000001750.jpg',
    'http://images.cocodataset.org/test-stuff2017/000000000001.jpg'
]

image_processor = transforms.Compose(
    [
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.47853944, 0.4732864, 0.47434163],
        ),
    ]
)

images = []
for url in urls:
    image = Image.open(requests.get(url, stream=True).raw).convert("RGB")
    image = image_processor(image)
    images.append(image)


model_id = 'docling-project/DocumentFigureClassifier-v2.5'

model = EfficientNetForImageClassification.from_pretrained(model_id)

labels = model.config.id2label

device = torch.device("cpu")

torch_images = torch.stack(images).to(device)

with torch.no_grad():
    logits = model(torch_images).logits  # (batch_size, num_classes)
    probs_batch = logits.softmax(dim=1)  # (batch_size, num_classes)
    probs_batch = probs_batch.cpu().numpy().tolist()

for idx, probs_image in enumerate(probs_batch):
    preds = [(labels[i], prob) for i, prob in enumerate(probs_image)]
    preds.sort(key=lambda t: t[1], reverse=True)
    print(f"{idx}: {preds}")
```


## How to use - ONNX

Example of how to classify an image into one of the 26 classes using onnx runtime:

```python
import onnxruntime

import numpy as np
import torchvision.transforms as transforms

from PIL import Image
import requests

LABELS = [
    "logo",
    "photograph",
    "icon",
    "engineering_drawing",
    "line_chart",
    "bar_chart",
    "other",
    "table",
    "flow_chart",
    "screenshot_from_computer",
    "signature",
    "screenshot_from_manual",
    "geographical_map",
    "pie_chart",
    "page_thumbnail",
    "stamp",
    "music",
    "calendar",
    "qr_code",
    "bar_code",
    "full_page_image",
    "scatter_plot",
    "chemistry_structure",
    "topographical_map",
    "crossword_puzzle",
    "box_plot"
]


urls = [
    'http://images.cocodataset.org/val2017/000000039769.jpg',
    'http://images.cocodataset.org/test-stuff2017/000000001750.jpg',
    'http://images.cocodataset.org/test-stuff2017/000000000001.jpg'
]

images = []
for url in urls:
    image = Image.open(requests.get(url, stream=True).raw).convert("RGB")
    images.append(image)


image_processor = transforms.Compose(
    [
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=[0.485, 0.456, 0.406],
            std=[0.47853944, 0.4732864, 0.47434163],
        ),
    ]
)


processed_images_onnx = [image_processor(image).unsqueeze(0) for image in images]

# onnx needs numpy as input
onnx_inputs = [item.numpy(force=True) for item in processed_images_onnx]

# pack into a batch
onnx_inputs = np.concatenate(onnx_inputs, axis=0)

ort_session = onnxruntime.InferenceSession(
    "./DocumentFigureClassifier-v2_5-onnx/model.onnx",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
)


for item in ort_session.run(None, {'input': onnx_inputs}):
    for x in iter(item):
        pred = x.argmax()
        print(LABELS[pred])
```


## Training Data

This model was trained on a subset of the subset of HuggingFace/finepdfs, a large-scale dataset for document understanding tasks.


## Citation

If you use this model in your work, please cite the following papers:

```
@article{Tan2019EfficientNetRM,
  title={EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks},
  author={Mingxing Tan and Quoc V. Le},
  journal={ArXiv},
  year={2019},
  volume={abs/1905.11946}
}

@techreport{Docling,
  author = {Deep Search Team},
  month = {8},
  title = {{Docling Technical Report}},
  url={https://arxiv.org/abs/2408.09869},
  eprint={2408.09869},
  doi = "10.48550/arXiv.2408.09869",
  version = {1.0.0},
  year = {2024}
}