"""ESM2 predict masked amino acid — Lyceum version.

Input a fasta with format:
>1
MA<mask>GMT

Returns a tsv file of most probable amino acids.

Usage on Lyceum:
    lyceum python run lyceum_esm2.py -r requirements/esm2.txt -m a100 \
        -- --input /job/work/input/test.faa --output-dir /job/work/output/esm2/
"""

import argparse
from pathlib import Path

import torch
import esm
import pandas as pd


def esm2_predict_masked(fasta_name, fasta_str, output_dir, make_figures=False):
    """Run ESM2 masked position prediction.

    Args:
        fasta_name: Name of the input fasta file (used for output naming).
        fasta_str: Contents of the fasta file.
        output_dir: Directory to write results to.
        make_figures: If True, generate contact map PNGs.

    Returns:
        List of output file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load ESM-2 model
    model, alphabet = esm.pretrained.esm2_t33_650M_UR50D()
    batch_converter = alphabet.get_batch_converter()
    model.eval()  # disables dropout for deterministic results

    assert fasta_str.startswith(">"), f"{fasta_name} is not a fasta file"

    data = []
    for entry in fasta_str[1:].split("\n>"):
        label, _, seq = entry.partition("\n")
        seq = seq.replace("\n", "").strip()
        data.append((label, seq))

    _batch_labels, _batch_strs, batch_tokens = batch_converter(data)

    results_list = []
    with torch.no_grad():
        results = model(batch_tokens, repr_layers=[33], return_contacts=True)

    for i, (label, seq) in enumerate(data):
        # Find the position of the mask token for this sequence
        mask_position = (batch_tokens[i] == alphabet.mask_idx).nonzero(as_tuple=True)[0][0]

        # Get logits for the masked position
        logits = results["logits"][i, mask_position]

        # Convert logits to probabilities
        probs = torch.nn.functional.softmax(logits, dim=0)

        # Get the top 5 predictions
        top_probs, top_indices = probs.topk(5)

        all_probs, all_indices = probs.sort(descending=True)
        for prob, idx in zip(all_probs, all_indices):
            aa = alphabet.get_tok(idx)
            results_list.append((i, label, aa, round(float(prob), 4)))

        # Get the best prediction
        best_prediction = alphabet.get_tok(top_indices[0])
        best_probability = top_probs[0].item()
        print(f"\nBest prediction for '{label}': {best_prediction} {best_probability}\n")

        if make_figures:
            import matplotlib.pyplot as plt
            plt.figure(figsize=(10, 10))
            plt.matshow(results["contacts"][i].cpu())
            plt.title(f"Contact Map for {label}")
            plt.colorbar()
            plt.savefig(str(output_dir / f"{fasta_name}.contact_map_{label}.png"))
            plt.close()

    df = pd.DataFrame(results_list, columns=["seq_n", "label", "aa", "prob"])
    tsv_path = output_dir / f"{fasta_name}.results.tsv"
    df.to_csv(tsv_path, sep="\t", index=None)
    print(f"\nWrote results to {tsv_path}")

    output_files = list(output_dir.glob("**/*.*"))
    print(f"Output files: {[str(f) for f in output_files]}")
    return output_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESM2 masked position prediction")
    parser.add_argument("--input", required=True, help="Path to input FASTA file")
    parser.add_argument("--output-dir", default="/job/work/output/esm2", help="Output directory")
    parser.add_argument("--make-figures", action="store_true", help="Generate contact map PNGs")
    args = parser.parse_args()

    fasta_path = Path(args.input)
    fasta_str = fasta_path.read_text()
    fasta_name = fasta_path.stem

    esm2_predict_masked(fasta_name, fasta_str, args.output_dir, args.make_figures)
