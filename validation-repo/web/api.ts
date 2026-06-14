// A tiny TS surface so the fixture is multi-language.

export interface Greeting {
  name: string;
  text: string;
}

export function greet(name: string): Greeting {
  return { name, text: `Hello, ${name}!` };
}

export function greetAll(names: string[]): Greeting[] {
  return names.map(greet);
}
