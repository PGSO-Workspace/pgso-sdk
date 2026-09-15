//! Drive timestamped validation traces through the real PGSO pipeline.
//! The expected column is validated as a label but never supplied to the pipeline.
use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use std::cell::RefCell;
use std::collections::{HashSet, VecDeque};
use std::env;
use std::error::Error;
use std::fs;
use std::io::{self, Write};
use std::rc::Rc;

type ReadingQueue = Rc<RefCell<VecDeque<Option<SignalReading>>>>;

struct Readings(ReadingQueue);

impl Signal for Readings {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0
            .borrow_mut()
            .pop_front()
            .flatten()
            .into_iter()
            .collect()
    }
}

fn pipeline() -> Result<(Pgso<Readings, LocalActuator>, ReadingQueue), Box<dyn Error>> {
    let protected = HashSet::from([ToolId::from("human")]);
    let queue = Rc::new(RefCell::new(VecDeque::new()));
    let pgso = Pgso::builder()
        .signal(Readings(Rc::clone(&queue)))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                confidence_threshold: 0.5,
                deviation_threshold: 0.3,
                hysteresis_window: 3,
                ema_alpha: 0.1,
                warmup_readings: 5,
                population_prior: 0.5,
            })?
            .with_max_gap_ms(1200)?,
        )
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "elevated",
                Axis::Arousal,
                0.3,
                0.5,
                vec![
                    Action::Prune(ToolId::from("quote")),
                    Action::Prune(ToolId::from("human")),
                    Action::InjectDirective("Clarify.".into()),
                ],
            )
            .with_direction(Direction::Rising)]),
            protected.clone(),
        ))
        .actuator(LocalActuator::new(
            Catalog::new(vec![
                Tool::new("quote", "Quote"),
                Tool::new("human", "Human"),
            ]),
            protected,
        ))
        .build()?;
    Ok((pgso, queue))
}

fn fields(line: &str, line_number: usize) -> Result<Vec<String>, Box<dyn Error>> {
    let mut fields = Vec::new();
    let mut field = String::new();
    let mut chars = line.chars().peekable();
    let mut quoted = false;
    let mut closed = false;
    while let Some(ch) = chars.next() {
        match ch {
            '"' if quoted && chars.peek() == Some(&'"') => {
                field.push('"');
                chars.next();
            }
            '"' if quoted => {
                quoted = false;
                closed = true;
            }
            '"' if field.is_empty() && !closed => quoted = true,
            '"' => return Err(invalid(line_number, "quote inside an unquoted field")),
            ',' if !quoted => {
                fields.push(std::mem::take(&mut field));
                closed = false;
            }
            _ if closed => return Err(invalid(line_number, "text after a closing quote")),
            _ => field.push(ch),
        }
    }
    if quoted {
        return Err(invalid(line_number, "unterminated quoted field"));
    }
    fields.push(field);
    Ok(fields)
}

fn invalid(line: usize, message: impl Into<String>) -> Box<dyn Error> {
    io::Error::new(
        io::ErrorKind::InvalidData,
        format!("CSV line {line}: {}", message.into()),
    )
    .into()
}

fn csv_field(value: &str) -> String {
    if value.contains([',', '"', '\n', '\r']) {
        format!("\"{}\"", value.replace('"', "\"\""))
    } else {
        value.to_owned()
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args_os().skip(1);
    let path = args
        .next()
        .filter(|_| args.next().is_none())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "usage: validation_trace INPUT.csv",
            )
        })?;
    let input = fs::read_to_string(path)?;
    run(&input, &mut io::stdout().lock())
}

fn run(input: &str, output: &mut impl Write) -> Result<(), Box<dyn Error>> {
    let mut lines = input.lines().peekable();
    let header = lines
        .next()
        .ok_or_else(|| invalid(1, "missing header"))?
        .trim_end_matches('\r');
    if header != "scenario,step,timestamp_ms,value,confidence,expected" {
        return Err(invalid(
            1,
            "expected header scenario,step,timestamp_ms,value,confidence,expected",
        ));
    }
    if lines.peek().is_none() {
        return Err(invalid(2, "at least one observation is required"));
    }

    let quote = ToolId::from("quote");
    let human = ToolId::from("human");
    let mut current_scenario = String::new();
    let mut completed = HashSet::new();
    let (mut pgso, mut queue) = pipeline()?;
    writeln!(
        output,
        "scenario,step,governed,protected_present,directive_count,transitions"
    )?;

    for (index, raw_line) in lines.enumerate() {
        let line_number = index + 2;
        let row = fields(raw_line.trim_end_matches('\r'), line_number)?;
        if row.len() != 6 {
            return Err(invalid(
                line_number,
                format!("expected 6 fields, got {}", row.len()),
            ));
        }
        let scenario = row[0].trim();
        if scenario.is_empty() {
            return Err(invalid(line_number, "scenario is empty"));
        }
        let step: u64 = row[1]
            .trim()
            .parse()
            .map_err(|_| invalid(line_number, "step must be an unsigned integer"))?;
        let timestamp_ms: u64 = row[2]
            .trim()
            .parse()
            .map_err(|_| invalid(line_number, "timestamp_ms must be an unsigned integer"))?;
        if !matches!(row[5].trim(), "" | "0" | "1" | "false" | "true") {
            return Err(invalid(line_number, "expected must be empty or binary"));
        }

        if scenario != current_scenario {
            if !current_scenario.is_empty() {
                completed.insert(std::mem::take(&mut current_scenario));
            }
            if completed.contains(scenario) {
                return Err(invalid(line_number, "scenario rows must be contiguous"));
            }
            current_scenario = scenario.to_owned();
            (pgso, queue) = pipeline()?;
        }

        let reading = if row[3].trim().is_empty() {
            if !row[4].trim().is_empty() {
                return Err(invalid(
                    line_number,
                    "confidence must also be empty for silence",
                ));
            }
            None
        } else {
            let value: f32 = row[3]
                .trim()
                .parse()
                .map_err(|_| invalid(line_number, "value must be a number or empty for silence"))?;
            if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                return Err(invalid(line_number, "value must be finite and in [0, 1]"));
            }
            let confidence: f32 = row[4]
                .trim()
                .parse()
                .map_err(|_| invalid(line_number, "confidence must be a number"))?;
            if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
                return Err(invalid(
                    line_number,
                    "confidence must be finite and in [0, 1]",
                ));
            }
            Some(SignalReading {
                value,
                axis: Axis::Arousal,
                confidence,
                timestamp_ms,
            })
        };
        queue.borrow_mut().push_back(reading);
        let catalog = pgso.process_window(&AudioWindow {
            samples: Vec::new(),
            sample_rate: 16_000,
            timestamp_ms,
        })?;
        writeln!(
            output,
            "{},{},{},{},{},{}",
            csv_field(scenario),
            step,
            u8::from(!catalog.contains(&quote)),
            u8::from(catalog.contains(&human)),
            pgso.actuator().directives().len(),
            pgso.audit_log().transitions().len()
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_malformed_traces_and_does_not_use_expected_labels() {
        const HEADER: &str = "scenario,step,timestamp_ms,value,confidence,expected\n";
        for row in [
            "",
            "a\"b\",0,0,0.5,0.9,0",
            "\"a\"b,0,0,0.5,0.9,0",
            "\"a,0,0,0.5,0.9,0",
            "a,0,0,NaN,0.9,0",
            "a,0,0,0.5,2,0",
            "a,0,0,,0.9,0",
            "a,0,0,0.5,0.9,2",
            "a,0,0,0.5,0.9,0,extra",
            "a,0,0,0.5,0.9,0\nb,0,0,0.5,0.9,0\na,1,1,0.5,0.9,0",
        ] {
            assert!(
                run(&format!("{HEADER}{row}"), &mut Vec::new()).is_err(),
                "{row:?}"
            );
        }
        let mut outputs = Vec::new();
        for label in ["0", "1", ""] {
            let mut output = Vec::new();
            let rows = format!(
                "\"a,\"\"b\",0,0,0.95,0.9,{label}\n\"a,\"\"b\",1,500,0.95,0.9,{label}\n\"a,\"\"b\",2,1000,0.95,0.9,{label}\n"
            );
            run(&format!("{HEADER}{rows}"), &mut output).unwrap();
            outputs.push(output);
        }
        assert_eq!(outputs[0], outputs[1]);
        assert_eq!(outputs[1], outputs[2]);
        assert!(String::from_utf8(outputs.remove(0))
            .unwrap()
            .ends_with("\"a,\"\"b\",2,1,1,1,1\n"));
    }
}
