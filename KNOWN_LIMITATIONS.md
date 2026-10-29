# Known limitations

- The supplied pack does not provide a supported event for G10 or a valid `tv_app_open` event for G11; these remain explicitly unsupported rather than being inferred.
- This implementation uses deterministic local logic and a mock provider; it does not claim to model a production LLM or production data plane.
- The pilot summary compares served viewers to the broader population and therefore suffers from selection bias; randomized assignment is the valid basis for group comparisons.
- The supplied dataset is synthetic and cannot support causal claims beyond the randomized design and observed attributable signals.
