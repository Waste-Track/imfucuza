# Research record

Run in order on Google Colab. Every notebook reads and writes Google Drive (`MyDrive/ics591_*`), fixes its random
seeds, and saves its results files next to its outputs. Images and model weights are **not** in this repo:
the scraped images are for research use only, and the weights live in the private model registry.

| # | Notebook | What it decides | Key result |
|---|---|---|---|
| 01 | `01_model_selection_five_models.ipynb` | Which architecture: MobileNetV3, EfficientNet-B0, ResNet-50, ConvNeXt-Tiny or ViT-S/16, trained identically on RealWaste (70/15/15 stratified split, seed 42) | ViT-S/16 and ConvNeXt-Tiny tie (macro-F1 0.953 / 0.949, 95% CIs overlap); both beat the other three (FAR 2.6–3.1% vs 9.6–15.1%) |
| 02 | `02_tiebreak_vit_vs_convnext.ipynb` | Tie-break over 3 training seeds, with a decision rule fixed in advance; temperature scaling | ViT-S/16 selected (smaller; FAR within 0.17 pp); calibration error reduced about 70% |
| 03 | `03_local_dataset_collection.ipynb` | Collect Ghana-specific web images: keyword search, download, near-duplicate removal, CLIP pre-screen | About 1,100 unique candidates, with source URLs recorded |
| 04 | `04_model_assisted_labelling.ipynb` | Human test set (blind, majority vote), seed labels, model-assisted rounds with audit | Cohen's kappa 0.65 (2 labellers, n = 150); about 30% of images unusable; auto-labelling only met the 95% bar for `discard` |
| 05 | `05_finetune_v1.ipynb` | Two-head model (material + cleanliness) on RealWaste + local labels; coverage vs accuracy | Pay/no-pay 91.2%, clean-plastic recall 14/14, 4-way decision 60.2% (n = 113); RealWaste baseline beat v1 on plastic vs non-plastic (72% vs 64%, within noise) |
| 06 | `06_deploy_service_render.ipynb` | ONNX export, parity and contract checks, publish to the model registry, deploy | ONNX = PyTorch to under 1e-3; service reproduces the offline result; Render free tier |
