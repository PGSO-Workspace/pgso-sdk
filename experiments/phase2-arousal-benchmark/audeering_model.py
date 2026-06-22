"""
audeering wav2vec2-large-robust msp-dim A/D/V model (HEAVY reference), ported
from the official model card so the cached weights load unchanged.

Source: https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim
LICENSE: CC BY-NC-SA 4.0 -- RESEARCH ONLY. Academic ceiling, NOT shippable.
Output order: [arousal, dominance, valence] in [0, 1]. We use AROUSAL.
"""
import numpy as np
import torch
from torch import nn
from transformers import Wav2Vec2Processor
from transformers.models.wav2vec2.modeling_wav2vec2 import Wav2Vec2Model, Wav2Vec2PreTrainedModel

HF_ID = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"


class RegressionHead(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.final_dropout)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features):
        x = self.dropout(features)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        return self.out_proj(x)


class EmotionModel(Wav2Vec2PreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.wav2vec2 = Wav2Vec2Model(config)
        self.classifier = RegressionHead(config)
        self.init_weights()

    def forward(self, input_values):
        hidden = self.wav2vec2(input_values)[0]
        hidden = torch.mean(hidden, dim=1)
        return self.classifier(hidden)


def load(device: str = "cpu"):
    processor = Wav2Vec2Processor.from_pretrained(HF_ID)
    model = EmotionModel.from_pretrained(HF_ID).to(device).eval()
    return processor, model


def predict_adv(processor, model, samples_16k, device: str = "cpu") -> np.ndarray:
    """Raw 16 kHz mono float samples -> np.array([arousal, dominance, valence])."""
    inputs = processor(np.asarray(samples_16k, dtype=np.float32), sampling_rate=16000, return_tensors="pt")
    iv = inputs.input_values.to(device)
    with torch.no_grad():
        logits = model(iv)
    return logits[0].detach().cpu().numpy()
